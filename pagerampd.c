/*
 * pagerampd - Audio playback daemon for PagerAmp (WiFi Pineapple Pager)
 *
 * Decodes MP3/WAV files and outputs S16LE PCM to stdout for piping to aplay.
 * Controlled via named FIFO commands, reports status via JSON on status FIFO.
 *
 * Architecture:
 *   pageramp.py → /tmp/pageramp.cmd (commands) → pagerampd → stdout (PCM) → aplay
 *   pagerampd → /tmp/pageramp.status (JSON) → pageramp.py
 *
 * Usage:
 *   mkfifo /tmp/pageramp.cmd /tmp/pageramp.status
 *   ./pagerampd | aplay -D bluealsa -f S16_LE -r 44100 -c 2 -
 */

#define MINIMP3_IMPLEMENTATION
#ifndef MINIMP3_ONLY_MP3
#define MINIMP3_ONLY_MP3
#endif
#ifndef MINIMP3_NO_SIMD
#define MINIMP3_NO_SIMD
#endif
#include "minimp3.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <sys/stat.h>
#include <sys/time.h>

/* --- Configuration --- */
#define CMD_FIFO_PATH   "/tmp/pageramp.cmd"
#define STATUS_FIFO_PATH "/tmp/pageramp.status"
#define READ_BUF_SIZE   (16 * 1024)
#define MAX_BUF_SIZE    (2 * 1024 * 1024)
#define CMD_BUF_SIZE    512
#define MAX_PLAYLIST    256
#define STATUS_INTERVAL_MS 250

/* Volume: Q15 fixed-point (0-100 → 0-32768) */
#define VOL_SHIFT 15
#define VOL_MAX   32768

/* --- State --- */
typedef enum {
    STATE_STOPPED,
    STATE_PLAYING,
    STATE_PAUSED
} PlayState;

static const char *state_names[] = {"stopped", "playing", "paused"};

typedef struct {
    char path[256];
} Track;

typedef struct {
    /* Playback state */
    PlayState state;
    int volume;             /* 0-100 */
    int vol_factor;         /* Q15 fixed-point multiplier */

    /* Current file */
    FILE *fp;
    char current_file[256];
    long file_size;
    int duration;           /* estimated seconds */
    int position;           /* current seconds */
    int sample_rate;
    int channels;

    /* MP3 decoder */
    mp3dec_t dec;
    unsigned char *mp3_buf;
    size_t mp3_buf_size;
    size_t mp3_buf_filled;
    size_t mp3_buf_consumed;
    long bytes_decoded;     /* bytes consumed from file */
    int is_wav;

    /* WAV state */
    int wav_data_offset;
    int wav_data_size;

    /* Playlist */
    Track playlist[MAX_PLAYLIST];
    int playlist_len;
    int playlist_idx;

    /* IPC */
    int cmd_fd;
    int status_fd;
    char cmd_line[CMD_BUF_SIZE];
    int cmd_line_len;

    /* Timing */
    long long last_status_ms;
    int samples_written;    /* samples since last position update */

    /* Control */
    int running;
} Daemon;

static Daemon g;

/* --- Utility --- */

static long long now_ms(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (long long)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

static void set_volume(int vol)
{
    if (vol < 0) vol = 0;
    if (vol > 100) vol = 100;
    g.volume = vol;
    /* Q15: factor = vol * 32768 / 100 */
    g.vol_factor = (vol * VOL_MAX) / 100;
}

static void apply_volume(mp3d_sample_t *samples, int count)
{
    int i;
    if (g.vol_factor >= VOL_MAX) return; /* skip if 100% */
    for (i = 0; i < count; i++) {
        samples[i] = (mp3d_sample_t)((samples[i] * g.vol_factor) >> VOL_SHIFT);
    }
}

/* --- WAV Parsing --- */

static int parse_wav_header(FILE *fp)
{
    unsigned char hdr[44];
    int audio_fmt, bits, data_size;

    if (fread(hdr, 1, 44, fp) != 44)
        return -1;

    /* Check RIFF/WAVE magic */
    if (memcmp(hdr, "RIFF", 4) != 0 || memcmp(hdr + 8, "WAVE", 4) != 0)
        return -1;

    /* Check fmt chunk */
    if (memcmp(hdr + 12, "fmt ", 4) != 0)
        return -1;

    audio_fmt = hdr[20] | (hdr[21] << 8);
    if (audio_fmt != 1) /* PCM only */
        return -1;

    g.channels = hdr[22] | (hdr[23] << 8);
    g.sample_rate = hdr[24] | (hdr[25] << 8) | (hdr[26] << 16) | (hdr[27] << 24);
    bits = hdr[34] | (hdr[35] << 8);

    if (bits != 16)
        return -1;

    /* Find data chunk - may not be at offset 36 */
    /* Simple: assume standard 44-byte header */
    if (memcmp(hdr + 36, "data", 4) != 0) {
        /* Scan for data chunk */
        unsigned char scan[8];
        fseek(fp, 12, SEEK_SET);
        while (fread(scan, 1, 8, fp) == 8) {
            int chunk_size = scan[4] | (scan[5] << 8) | (scan[6] << 16) | (scan[7] << 24);
            if (memcmp(scan, "data", 4) == 0) {
                g.wav_data_offset = (int)ftell(fp);
                g.wav_data_size = chunk_size;
                data_size = chunk_size;
                goto found;
            }
            fseek(fp, chunk_size, SEEK_CUR);
        }
        return -1;
    }

    data_size = hdr[40] | (hdr[41] << 8) | (hdr[42] << 16) | (hdr[43] << 24);
    g.wav_data_offset = 44;
    g.wav_data_size = data_size;

found:
    g.duration = data_size / (g.sample_rate * g.channels * 2);
    return 0;
}

/* --- File Management --- */

static void close_current(void)
{
    if (g.fp) {
        fclose(g.fp);
        g.fp = NULL;
    }
    g.current_file[0] = '\0';
    g.file_size = 0;
    g.duration = 0;
    g.position = 0;
    g.bytes_decoded = 0;
    g.samples_written = 0;
    g.mp3_buf_filled = 0;
    g.mp3_buf_consumed = 0;
    g.is_wav = 0;
    g.wav_data_offset = 0;
    g.wav_data_size = 0;
    g.sample_rate = 44100;
    g.channels = 2;
}

static int is_wav_file(const char *path)
{
    size_t len = strlen(path);
    if (len < 4) return 0;
    return (strcasecmp(path + len - 4, ".wav") == 0);
}

static int open_file(const char *path)
{
    FILE *fp;
    long sz;

    close_current();

    fp = fopen(path, "rb");
    if (!fp) {
        fprintf(stderr, "pagerampd: cannot open %s\n", path);
        return -1;
    }

    fseek(fp, 0, SEEK_END);
    sz = ftell(fp);
    fseek(fp, 0, SEEK_SET);

    g.fp = fp;
    g.file_size = sz;
    strncpy(g.current_file, path, sizeof(g.current_file) - 1);
    g.current_file[sizeof(g.current_file) - 1] = '\0';

    if (is_wav_file(path)) {
        g.is_wav = 1;
        if (parse_wav_header(fp) < 0) {
            fprintf(stderr, "pagerampd: invalid WAV: %s\n", path);
            close_current();
            return -1;
        }
        fprintf(stderr, "pagerampd: WAV %d Hz, %d ch\n", g.sample_rate, g.channels);
    } else {
        /* MP3 — will detect rate on first frame */
        mp3dec_init(&g.dec);
        g.mp3_buf_filled = 0;
        g.mp3_buf_consumed = 0;
        g.is_wav = 0;
        /* Estimate duration: ~128kbps average */
        if (sz > 0) {
            g.duration = (int)(sz * 8 / 128000);
        }
    }

    return 0;
}

/* --- Playlist --- */

static int parse_m3u(const char *path)
{
    FILE *fp;
    char line[512];
    int count = 0;

    fp = fopen(path, "r");
    if (!fp) return -1;

    g.playlist_len = 0;
    while (fgets(line, sizeof(line), fp) && count < MAX_PLAYLIST) {
        /* Strip newline */
        size_t len = strlen(line);
        while (len > 0 && (line[len-1] == '\n' || line[len-1] == '\r'))
            line[--len] = '\0';

        /* Skip empty lines and comments */
        if (len == 0 || line[0] == '#')
            continue;

        strncpy(g.playlist[count].path, line, sizeof(g.playlist[count].path) - 1);
        g.playlist[count].path[sizeof(g.playlist[count].path) - 1] = '\0';
        count++;
    }

    fclose(fp);
    g.playlist_len = count;
    return count;
}

static int play_track(int idx)
{
    if (idx < 0 || idx >= g.playlist_len)
        return -1;

    g.playlist_idx = idx;
    if (open_file(g.playlist[idx].path) < 0)
        return -1;

    g.state = STATE_PLAYING;
    return 0;
}

static void next_track(void)
{
    if (g.playlist_len == 0) {
        g.state = STATE_STOPPED;
        close_current();
        return;
    }

    int next = g.playlist_idx + 1;
    if (next >= g.playlist_len) {
        /* End of playlist */
        g.state = STATE_STOPPED;
        close_current();
        return;
    }

    if (play_track(next) < 0) {
        /* Skip bad track, try next */
        g.playlist_idx = next;
        next_track();
    }
}

static void prev_track(void)
{
    if (g.playlist_len == 0) return;

    /* If >3 seconds in, restart current track */
    if (g.position > 3) {
        play_track(g.playlist_idx);
        return;
    }

    int prev = g.playlist_idx - 1;
    if (prev < 0) prev = 0;
    play_track(prev);
}

/* --- Seek --- */

static void seek_to(int target_sec)
{
    if (!g.fp || g.file_size <= 0) return;

    if (target_sec < 0) target_sec = 0;
    if (g.duration > 0 && target_sec > g.duration)
        target_sec = g.duration;

    if (g.is_wav) {
        /* WAV: exact byte offset */
        int byte_offset = target_sec * g.sample_rate * g.channels * 2;
        if (byte_offset > g.wav_data_size)
            byte_offset = g.wav_data_size;
        fseek(g.fp, g.wav_data_offset + byte_offset, SEEK_SET);
    } else {
        /* MP3: approximate byte offset */
        if (g.duration > 0) {
            long offset = (long)((double)target_sec / g.duration * g.file_size);
            if (offset >= g.file_size) offset = g.file_size - 1;
            fseek(g.fp, offset, SEEK_SET);
            /* Reset decoder state for clean sync */
            mp3dec_init(&g.dec);
        }
    }

    g.mp3_buf_filled = 0;
    g.mp3_buf_consumed = 0;
    g.bytes_decoded = ftell(g.fp);
    g.position = target_sec;
    g.samples_written = 0;
}

/* --- Decode & Output --- */

/* Resample mono to stereo, or 22050→44100 by duplication */
static int resample_output(mp3d_sample_t *pcm, int samples, int channels, int rate)
{
    static mp3d_sample_t out[MINIMP3_MAX_SAMPLES_PER_FRAME * 4];
    int i, total;

    if (rate == 44100 && channels == 2) {
        /* No resampling needed */
        apply_volume(pcm, samples * channels);
        fwrite(pcm, sizeof(mp3d_sample_t), samples * channels, stdout);
        return samples;
    }

    total = 0;
    if (rate == 22050 || rate == 11025) {
        int dup = (rate == 11025) ? 4 : 2;
        for (i = 0; i < samples; i++) {
            int j;
            mp3d_sample_t l, r;
            if (channels == 2) {
                l = pcm[i * 2];
                r = pcm[i * 2 + 1];
            } else {
                l = r = pcm[i];
            }
            for (j = 0; j < dup; j++) {
                out[total++] = l;
                out[total++] = r;
            }
        }
    } else if (channels == 1) {
        /* Mono to stereo */
        for (i = 0; i < samples; i++) {
            out[total++] = pcm[i];
            out[total++] = pcm[i];
        }
    } else {
        /* Unsupported rate, just pass through */
        apply_volume(pcm, samples * channels);
        fwrite(pcm, sizeof(mp3d_sample_t), samples * channels, stdout);
        return samples;
    }

    apply_volume(out, total);
    fwrite(out, sizeof(mp3d_sample_t), total, stdout);
    return total / 2; /* return sample count at output rate */
}

/* Decode one MP3 frame and write PCM. Returns: 1=ok, 0=EOF, -1=error */
static int decode_mp3_frame(void)
{
    mp3dec_frame_info_t info;
    mp3d_sample_t pcm[MINIMP3_MAX_SAMPLES_PER_FRAME];
    int samples;
    size_t nr;

    /* Shift unconsumed data to front */
    if (g.mp3_buf_consumed > 0) {
        g.mp3_buf_filled -= g.mp3_buf_consumed;
        if (g.mp3_buf_filled > 0)
            memmove(g.mp3_buf, g.mp3_buf + g.mp3_buf_consumed, g.mp3_buf_filled);
        g.mp3_buf_consumed = 0;
    }

    /* Read more data */
    if (g.mp3_buf_filled < g.mp3_buf_size) {
        nr = fread(g.mp3_buf + g.mp3_buf_filled, 1,
                   g.mp3_buf_size - g.mp3_buf_filled, g.fp);
        g.mp3_buf_filled += nr;
    }

    if (g.mp3_buf_filled == 0)
        return 0; /* EOF */

    samples = mp3dec_decode_frame(&g.dec, g.mp3_buf, (int)g.mp3_buf_filled,
                                  pcm, &info);

    if (info.frame_bytes == 0) {
        if (feof(g.fp))
            return 0;
        /* Grow buffer */
        if (g.mp3_buf_size < MAX_BUF_SIZE) {
            g.mp3_buf_size *= 2;
            g.mp3_buf = realloc(g.mp3_buf, g.mp3_buf_size);
            if (!g.mp3_buf) return -1;
        }
        return 1; /* try again */
    }

    g.mp3_buf_consumed = info.frame_bytes;
    g.bytes_decoded += info.frame_bytes;

    if (samples > 0) {
        if (g.sample_rate == 44100 && info.hz != 44100) {
            g.sample_rate = info.hz;
            g.channels = info.channels;
            /* Recalculate duration with actual bitrate */
            if (info.bitrate_kbps > 0 && g.file_size > 0) {
                g.duration = (int)(g.file_size * 8 / (info.bitrate_kbps * 1000));
            }
            fprintf(stderr, "pagerampd: MP3 %d Hz, %d ch, %d kbps\n",
                    info.hz, info.channels, info.bitrate_kbps);
        }

        int out_samples = resample_output(pcm, samples, info.channels, info.hz);
        g.samples_written += out_samples;

        /* Update position estimate based on byte offset */
        if (g.duration > 0 && g.file_size > 0) {
            g.position = (int)((double)g.bytes_decoded / g.file_size * g.duration);
        }
    }

    return 1;
}

/* Decode one chunk of WAV and write PCM */
static int decode_wav_chunk(void)
{
    mp3d_sample_t buf[4096];
    int to_read = sizeof(buf);
    long remaining;
    size_t nr;

    if (!g.fp) return 0;

    remaining = g.wav_data_offset + g.wav_data_size - ftell(g.fp);
    if (remaining <= 0) return 0;

    if (to_read > remaining) to_read = (int)remaining;

    nr = fread(buf, 1, to_read, g.fp);
    if (nr == 0) return 0;

    int sample_count = (int)(nr / (sizeof(mp3d_sample_t) * g.channels));

    if (g.sample_rate != 44100 || g.channels != 2) {
        resample_output(buf, sample_count, g.channels, g.sample_rate);
    } else {
        apply_volume(buf, sample_count * g.channels);
        fwrite(buf, 1, nr, stdout);
    }

    g.samples_written += sample_count;
    /* Update position */
    long data_pos = ftell(g.fp) - g.wav_data_offset;
    if (g.wav_data_size > 0) {
        g.position = (int)((double)data_pos / g.wav_data_size * g.duration);
    }

    return 1;
}

/* --- IPC --- */

static void open_fifos(void)
{
    /* Open command FIFO for non-blocking read */
    g.cmd_fd = open(CMD_FIFO_PATH, O_RDONLY | O_NONBLOCK);
    if (g.cmd_fd < 0) {
        fprintf(stderr, "pagerampd: cannot open %s: %s\n",
                CMD_FIFO_PATH, strerror(errno));
    }

    /* Status FIFO opened per-write to handle reader absence */
    g.status_fd = -1;
}

static void write_status(void)
{
    char buf[512];
    int fd, len;

    /* Extract filename from path */
    const char *fname = g.current_file;
    const char *p = strrchr(g.current_file, '/');
    if (p) fname = p + 1;

    len = snprintf(buf, sizeof(buf),
        "{\"state\":\"%s\",\"file\":\"%s\",\"pos\":%d,\"dur\":%d,"
        "\"vol\":%d,\"track\":%d,\"total\":%d,\"rate\":%d}\n",
        state_names[g.state],
        fname,
        g.position,
        g.duration,
        g.volume,
        g.playlist_idx + 1,
        g.playlist_len,
        g.sample_rate);

    fd = open(STATUS_FIFO_PATH, O_WRONLY | O_NONBLOCK);
    if (fd >= 0) {
        write(fd, buf, len);
        close(fd);
    }
    /* Silently ignore if no reader */
}

static void process_command(char *cmd)
{
    /* Trim whitespace */
    while (*cmd == ' ' || *cmd == '\t') cmd++;
    size_t len = strlen(cmd);
    while (len > 0 && (cmd[len-1] == '\n' || cmd[len-1] == '\r' || cmd[len-1] == ' '))
        cmd[--len] = '\0';

    if (len == 0) return;

    fprintf(stderr, "pagerampd: cmd: %s\n", cmd);

    if (strncmp(cmd, "PLAY ", 5) == 0) {
        const char *path = cmd + 5;
        /* Single file play: create 1-track playlist */
        strncpy(g.playlist[0].path, path, sizeof(g.playlist[0].path) - 1);
        g.playlist[0].path[sizeof(g.playlist[0].path) - 1] = '\0';
        g.playlist_len = 1;
        play_track(0);

    } else if (strcmp(cmd, "PAUSE") == 0) {
        if (g.state == STATE_PLAYING)
            g.state = STATE_PAUSED;

    } else if (strcmp(cmd, "RESUME") == 0) {
        if (g.state == STATE_PAUSED)
            g.state = STATE_PLAYING;

    } else if (strcmp(cmd, "TOGGLE") == 0) {
        if (g.state == STATE_PLAYING)
            g.state = STATE_PAUSED;
        else if (g.state == STATE_PAUSED)
            g.state = STATE_PLAYING;

    } else if (strcmp(cmd, "STOP") == 0) {
        g.state = STATE_STOPPED;
        close_current();

    } else if (strcmp(cmd, "NEXT") == 0) {
        next_track();

    } else if (strcmp(cmd, "PREV") == 0) {
        prev_track();

    } else if (strncmp(cmd, "SEEK ", 5) == 0) {
        const char *arg = cmd + 5;
        int target;
        if (arg[0] == '+' || arg[0] == '-') {
            target = g.position + atoi(arg);
        } else {
            target = atoi(arg);
        }
        seek_to(target);

    } else if (strncmp(cmd, "VOL ", 4) == 0) {
        const char *arg = cmd + 4;
        int vol;
        if (arg[0] == '+' || arg[0] == '-') {
            vol = g.volume + atoi(arg);
        } else {
            vol = atoi(arg);
        }
        set_volume(vol);

    } else if (strncmp(cmd, "PLAYLIST ", 9) == 0) {
        const char *path = cmd + 9;
        if (parse_m3u(path) > 0) {
            play_track(0);
        }

    } else if (strncmp(cmd, "QUEUE ", 6) == 0) {
        const char *path = cmd + 6;
        if (g.playlist_len < MAX_PLAYLIST) {
            strncpy(g.playlist[g.playlist_len].path, path,
                    sizeof(g.playlist[g.playlist_len].path) - 1);
            g.playlist[g.playlist_len].path[sizeof(g.playlist[g.playlist_len].path) - 1] = '\0';
            g.playlist_len++;
        }

    } else if (strncmp(cmd, "JUMP ", 5) == 0) {
        /* Jump to playlist index (0-based) */
        int idx = atoi(cmd + 5);
        play_track(idx);

    } else if (strcmp(cmd, "STATUS") == 0) {
        write_status();

    } else if (strcmp(cmd, "QUIT") == 0) {
        g.running = 0;
    }
}

static void poll_commands(void)
{
    char buf[CMD_BUF_SIZE];
    ssize_t n;

    if (g.cmd_fd < 0) {
        /* Try to reopen (reader may have disconnected) */
        g.cmd_fd = open(CMD_FIFO_PATH, O_RDONLY | O_NONBLOCK);
        if (g.cmd_fd < 0) return;
    }

    n = read(g.cmd_fd, buf, sizeof(buf) - 1);
    if (n > 0) {
        buf[n] = '\0';
        /* Process line by line */
        char *line = buf;
        char *nl;
        while ((nl = strchr(line, '\n')) != NULL) {
            *nl = '\0';
            /* Append to partial line buffer */
            int llen = (int)(nl - line);
            if (g.cmd_line_len + llen < CMD_BUF_SIZE - 1) {
                memcpy(g.cmd_line + g.cmd_line_len, line, llen);
                g.cmd_line_len += llen;
            }
            g.cmd_line[g.cmd_line_len] = '\0';
            process_command(g.cmd_line);
            g.cmd_line_len = 0;
            line = nl + 1;
        }
        /* Save partial line */
        int rem = (int)(buf + n - line);
        if (rem > 0 && g.cmd_line_len + rem < CMD_BUF_SIZE - 1) {
            memcpy(g.cmd_line + g.cmd_line_len, line, rem);
            g.cmd_line_len += rem;
        }
    } else if (n == 0) {
        /* Writer closed — reopen FIFO */
        close(g.cmd_fd);
        g.cmd_fd = open(CMD_FIFO_PATH, O_RDONLY | O_NONBLOCK);
    }
    /* n < 0 && errno == EAGAIN: no data, that's fine */
}

/* --- Signal Handling --- */

static void sig_handler(int sig)
{
    (void)sig;
    g.running = 0;
}

/* --- Main Loop --- */

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    /* Initialize state */
    memset(&g, 0, sizeof(g));
    g.state = STATE_STOPPED;
    g.running = 1;
    g.cmd_fd = -1;
    g.status_fd = -1;
    g.sample_rate = 44100;
    g.channels = 2;
    set_volume(80);

    /* Allocate MP3 decode buffer */
    g.mp3_buf_size = READ_BUF_SIZE;
    g.mp3_buf = malloc(g.mp3_buf_size);
    if (!g.mp3_buf) {
        fprintf(stderr, "pagerampd: out of memory\n");
        return 1;
    }

    signal(SIGPIPE, SIG_IGN);
    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);

    fprintf(stderr, "pagerampd: starting (pid %d)\n", getpid());

    open_fifos();

    while (g.running) {
        /* 1. Check for commands */
        poll_commands();

        if (!g.running) break;

        /* 2. Decode audio if playing */
        if (g.state == STATE_PLAYING && g.fp) {
            int result;
            if (g.is_wav) {
                result = decode_wav_chunk();
            } else {
                result = decode_mp3_frame();
            }

            if (result == 0) {
                /* Track ended — play next */
                fflush(stdout);
                next_track();
            }
            /* Flush periodically */
            fflush(stdout);
        } else {
            /* Not playing — sleep to avoid busy wait */
            usleep(50000); /* 50ms */
        }

        /* 3. Send status update periodically */
        long long now = now_ms();
        if (now - g.last_status_ms >= STATUS_INTERVAL_MS) {
            write_status();
            g.last_status_ms = now;
        }
    }

    fprintf(stderr, "pagerampd: shutting down\n");

    close_current();
    free(g.mp3_buf);

    if (g.cmd_fd >= 0) close(g.cmd_fd);

    return 0;
}
