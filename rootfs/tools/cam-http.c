// SPDX-License-Identifier: GPL-2.0
/*
 * cam-http: MJPEG over HTTP from the fh8626-camera driver.
 *
 *   cam-http [-p PORT] [-r FPS] [-q QUALITY] [-d /dev/video0]
 *            [-i auto|day|night] [-N NIGHT] [-D DAY]
 *
 *   /               HTML page (PAGE_FILE, or a bare stream page without it)
 *   /stream         multipart/x-mixed-replace MJPEG (browsers, VLC, ffmpeg,
 *                   Home Assistant "MJPEG IP camera")
 *   /snapshot.jpg   one JPEG
 *   /status         JSON: motor positions, day/night state, exposure
 *   /ptz?pan=N&tilt=N   move (N absolute, +N/-N relative, half-steps);
 *                   also stop=1, home=1, center=1. Answers with /status.
 *   /ir?mode=auto|day|night   day/night control. Answers with /status.
 *
 * Day/night (auto): the brightness measure is exposure lines x analogue
 * gain (gain 1 = 32, read from the sensor subdevice), "light" below. Night
 * (IR-cut filter out, IR LED on, saturation 0) when light >= NIGHT for
 * NIGHT_HOLD_S seconds; day again when light <= DAY for DAY_HOLD_S seconds.
 * There is no light sensor on the board, and the IR LED lights the scene in
 * night mode, so DAY is far below NIGHT; if the camera falls back to night
 * soon after switching to day, DAY is halved. Decisions only run while the
 * camera streams.
 *
 * Single-threaded, poll() based. The camera runs only while clients are
 * connected (it stops CAM_IDLE_S seconds after the last one has left); a
 * snapshot from a stopped camera waits CAM_SETTLE frames for auto exposure
 * and white balance. Frames are thinned to FPS. Every client sends from its
 * own buffer without blocking; a client still busy with the previous frame
 * skips the new one, so a slow Wi-Fi client does not hold up the others.
 */

#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#include <linux/videodev2.h>

#define MAX_CLIENTS	8
#define NBUFS		4
#define CAM_IDLE_S	5
#define CAM_SETTLE	30		/* frames before a snapshot of a cold camera */
#define REQ_MAX		2048
#define BOUNDARY	"fh8626frame"
#define PAGE_FILE	"/usr/share/cam-http/index.html"
#define PAGE_MAX	65536
#define STEPPER		"/sys/class/stepper/"
#define IRCUT_MODE	"/sys/devices/platform/ircut/mode"
#define IR_LED		"/sys/class/leds/ir/brightness"
#define GAIN_ONE	32
#define NIGHT_HOLD_S	5
#define DAY_HOLD_S	10
#define SWITCH_HOLD_S	30		/* no switch sooner after the last one */
#define FLAPPING_S	120		/* back to night within this: halve DAY */
#define DAY_MIN		50
#define AE_SETTLE_S	3		/* after the camera start */

/* C_DONE: close once the pending output is sent */
enum { C_FREE, C_REQ, C_STREAM, C_SNAP, C_DONE, C_CLOSE };

struct client {
	int fd, state;
	char req[REQ_MAX];
	int reqlen;
	char *out;			/* pending output */
	size_t outlen, outpos, outcap;
};

static struct client clients[MAX_CLIENTS];
static const char *dev = "/dev/video0";
static int vfd = -1, quality = 60;
static double fps = 10;
static void *mem[NBUFS];
static size_t memlen[NBUFS];
static unsigned int nbufs, frames_since_start;
static double last_sent, idle_since, cam_started;

/* day/night */
enum { IR_AUTO, IR_DAY, IR_NIGHT };
static const char *const ir_names[] = { "auto", "day", "night" };
static int ir_cfg = IR_AUTO, night, sdfd = -1;
static long night_thr = 8000, day_thr = 600, light = -1, exposure = -1, gain = -1;
static double cond_since, last_switch, auto_day_at;

static double now(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static int xioctl(int fd, unsigned long req, void *arg)
{
	int r;

	do
		r = ioctl(fd, req, arg);
	while (r < 0 && errno == EINTR);
	return r;
}

/* ---- sysfs ---------------------------------------------------------------- */

static int write_str(const char *path, const char *val)
{
	int fd = open(path, O_WRONLY), r;

	if (fd < 0)
		return -1;
	r = write(fd, val, strlen(val));
	close(fd);
	return r < 0 ? -1 : 0;
}

static long read_long(const char *path)
{
	char b[32] = "";
	int fd = open(path, O_RDONLY);
	ssize_t r;

	if (fd < 0)
		return -1;
	r = read(fd, b, sizeof(b) - 1);
	close(fd);
	return r > 0 ? strtol(b, NULL, 0) : -1;
}

static long axis_long(const char *axis, const char *attr)
{
	char p[96];

	snprintf(p, sizeof(p), STEPPER "%s/%s", axis, attr);
	return read_long(p);
}

static int axis_write(const char *axis, const char *attr, const char *val)
{
	char p[96];

	snprintf(p, sizeof(p), STEPPER "%s/%s", axis, attr);
	return write_str(p, val);
}

/* ---- camera ------------------------------------------------------------- */

static void set_ctrl(int fd, unsigned int id, int value)
{
	struct v4l2_control ctrl = { .id = id, .value = value };

	if (fd >= 0)
		xioctl(fd, VIDIOC_S_CTRL, &ctrl);
}

static int get_ctrl(int fd, unsigned int id, long *value)
{
	struct v4l2_control ctrl = { .id = id };

	if (fd < 0 || xioctl(fd, VIDIOC_G_CTRL, &ctrl) < 0)
		return -1;
	*value = ctrl.value;
	return 0;
}

/* The sensor subdevice: the first one with an exposure control */
static void sensor_open(void)
{
	char p[32];
	long v;
	int i;

	for (i = 0; i < 16 && sdfd < 0; i++) {
		snprintf(p, sizeof(p), "/dev/v4l-subdev%d", i);
		sdfd = open(p, O_RDWR);
		if (sdfd >= 0 && get_ctrl(sdfd, V4L2_CID_EXPOSURE, &v) < 0) {
			close(sdfd);
			sdfd = -1;
		}
	}
	if (sdfd < 0)
		fprintf(stderr, "cam-http: no sensor subdevice, day/night stays manual\n");
}

static void set_night(int n, const char *why)
{
	write_str(IRCUT_MODE, n ? "night" : "day");
	write_str(IR_LED, n ? "1" : "0");
	set_ctrl(vfd, V4L2_CID_SATURATION, n ? 0 : 128);
	if (n != night) {
		fprintf(stderr, "cam-http: %s (%s, light %ld)\n", n ? "night" : "day", why, light);
		last_switch = now();
	}
	night = n;
	cond_since = 0;
}

/* Once a second while the camera runs */
static void daynight_tick(void)
{
	double t = now();
	int want;

	if (get_ctrl(sdfd, V4L2_CID_EXPOSURE, &exposure) < 0 ||
	    get_ctrl(sdfd, V4L2_CID_ANALOGUE_GAIN, &gain) < 0)
		return;
	light = exposure * gain / GAIN_ONE;
	if (ir_cfg != IR_AUTO || t - cam_started < AE_SETTLE_S)
		return;

	want = night ? light <= day_thr : light >= night_thr;
	if (!want) {
		cond_since = 0;
		return;
	}
	if (!cond_since)
		cond_since = t;
	if (t - cond_since < (night ? DAY_HOLD_S : NIGHT_HOLD_S) ||
	    t - last_switch < SWITCH_HOLD_S)
		return;
	if (!night && auto_day_at && t - auto_day_at < FLAPPING_S && day_thr / 2 >= DAY_MIN) {
		day_thr /= 2;
		fprintf(stderr, "cam-http: back to night soon after day, day threshold now %ld\n",
			day_thr);
	}
	auto_day_at = night ? t : 0;		/* switching to day now */
	set_night(!night, "auto");
}

static void cam_stop(void)
{
	enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
	unsigned int i;

	if (vfd < 0)
		return;
	xioctl(vfd, VIDIOC_STREAMOFF, &type);
	for (i = 0; i < nbufs; i++)
		munmap(mem[i], memlen[i]);
	close(vfd);
	vfd = -1;
	fprintf(stderr, "cam-http: camera stopped\n");
}

static int cam_start(void)
{
	enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
	struct v4l2_requestbuffers req = { 0 };
	struct v4l2_control ctrl = { 0 };
	struct v4l2_format fmt = { 0 };
	unsigned int i;

	vfd = open(dev, O_RDWR | O_NONBLOCK);
	if (vfd < 0) {
		perror(dev);
		return -1;
	}
	fmt.type = type;
	xioctl(vfd, VIDIOC_G_FMT, &fmt);
	fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
	if (xioctl(vfd, VIDIOC_S_FMT, &fmt) < 0) {
		perror("VIDIOC_S_FMT");
		goto err;
	}
	ctrl.id = V4L2_CID_JPEG_COMPRESSION_QUALITY;
	ctrl.value = quality;
	xioctl(vfd, VIDIOC_S_CTRL, &ctrl);
	set_ctrl(vfd, V4L2_CID_SATURATION, night ? 0 : 128);

	req.count = NBUFS;
	req.type = type;
	req.memory = V4L2_MEMORY_MMAP;
	if (xioctl(vfd, VIDIOC_REQBUFS, &req) < 0) {
		perror("VIDIOC_REQBUFS");
		goto err;
	}
	nbufs = req.count;
	for (i = 0; i < nbufs; i++) {
		struct v4l2_buffer buf = { .type = type, .memory = V4L2_MEMORY_MMAP, .index = i };

		if (xioctl(vfd, VIDIOC_QUERYBUF, &buf) < 0)
			goto err;
		memlen[i] = buf.length;
		mem[i] = mmap(NULL, buf.length, PROT_READ, MAP_SHARED, vfd, buf.m.offset);
		if (mem[i] == MAP_FAILED || xioctl(vfd, VIDIOC_QBUF, &buf) < 0)
			goto err;
	}
	if (xioctl(vfd, VIDIOC_STREAMON, &type) < 0) {
		perror("VIDIOC_STREAMON");
		goto err;
	}
	frames_since_start = 0;
	cam_started = now();
	cond_since = 0;
	fprintf(stderr, "cam-http: camera started (%dx%d MJPEG, quality %d)\n",
		fmt.fmt.pix.width, fmt.fmt.pix.height, quality);
	return 0;
err:
	close(vfd);
	vfd = -1;
	return -1;
}

/* ---- clients ------------------------------------------------------------ */

static void client_close(struct client *c)
{
	close(c->fd);
	free(c->out);
	memset(c, 0, sizeof(*c));
	c->state = C_FREE;
}

/* Replace the pending output (only called when nothing is pending) */
static int client_set(struct client *c, const char *hdr, size_t hlen,
		      const void *body, size_t blen, const char *tail, size_t tlen)
{
	size_t n = hlen + blen + tlen;

	if (n > c->outcap) {
		char *p = realloc(c->out, n);

		if (!p)
			return -1;
		c->out = p;
		c->outcap = n;
	}
	memcpy(c->out, hdr, hlen);
	if (blen)
		memcpy(c->out + hlen, body, blen);
	if (tlen)
		memcpy(c->out + hlen + blen, tail, tlen);
	c->outlen = n;
	c->outpos = 0;
	return 0;
}

static void client_flush(struct client *c)
{
	while (c->outpos < c->outlen) {
		ssize_t r = send(c->fd, c->out + c->outpos, c->outlen - c->outpos,
				 MSG_DONTWAIT | MSG_NOSIGNAL);

		if (r < 0) {
			if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
				c->state = C_CLOSE;
			return;
		}
		c->outpos += r;
	}
	c->outpos = c->outlen = 0;
}

static const char page_min[] =
	"<!doctype html><html><head><meta charset=\"utf-8\">"
	"<meta name=\"viewport\" content=\"width=device-width\">"
	"<title>FH8626 camera</title><style>body{margin:0;background:#111}"
	"img{display:block;width:100%;height:auto}</style></head>"
	"<body><img src=\"/stream\" alt=\"camera\"></body></html>";

static void reply(struct client *c, const char *status, const char *type,
		  const void *body, size_t len)
{
	char hdr[256];
	int n = snprintf(hdr, sizeof(hdr), "HTTP/1.0 %s\r\nContent-Type: %s\r\n"
			 "Content-Length: %zu\r\nCache-Control: no-cache\r\n"
			 "Connection: close\r\n\r\n", status, type, len);

	client_set(c, hdr, n, body, len, NULL, 0);
	c->state = C_DONE;
}

static void reply_status(struct client *c)
{
	char b[512];
	int n = snprintf(b, sizeof(b),
		"{\"pan\":%ld,\"pan_target\":%ld,\"pan_max\":%ld,\"pan_moving\":%ld,"
		"\"tilt\":%ld,\"tilt_target\":%ld,\"tilt_max\":%ld,\"tilt_moving\":%ld,"
		"\"ir_mode\":\"%s\",\"night\":%d,\"streaming\":%d,"
		"\"exposure\":%ld,\"gain\":%ld,\"light\":%ld,"
		"\"night_threshold\":%ld,\"day_threshold\":%ld}\n",
		axis_long("pan", "position"), axis_long("pan", "target"),
		axis_long("pan", "max_steps"), axis_long("pan", "moving"),
		axis_long("tilt", "position"), axis_long("tilt", "target"),
		axis_long("tilt", "max_steps"), axis_long("tilt", "moving"),
		ir_names[ir_cfg], night, vfd >= 0, exposure, gain, light,
		night_thr, day_thr);

	reply(c, "200 OK", "application/json", b, n);
}

/* N, +N or -N */
static int valid_number(const char *v)
{
	if (*v == '+' || *v == '-')
		v++;
	if (!*v || strlen(v) > 9)
		return 0;
	for (; *v; v++)
		if (*v < '0' || *v > '9')
			return 0;
	return 1;
}

/* key=value&... ; returns 0 when every pair was understood */
static int do_query(const char *path, char *query)
{
	char *save, *kv;
	int ok = 0;

	for (kv = strtok_r(query, "&", &save); kv; kv = strtok_r(NULL, "&", &save)) {
		char *v = strchr(kv, '=');
		char b[24];

		if (!v)
			return -1;
		*v++ = 0;
		if (!strcmp(path, "/ptz")) {
			if ((!strcmp(kv, "pan") || !strcmp(kv, "tilt")) && valid_number(v)) {
				ok |= axis_write(kv, "target", v);
			} else if (!strcmp(kv, "stop")) {
				axis_write("pan", "stop", "1");
				axis_write("tilt", "stop", "1");
			} else if (!strcmp(kv, "home")) {
				ok |= axis_write("pan", "home", "1");
				ok |= axis_write("tilt", "home", "1");
			} else if (!strcmp(kv, "center")) {
				snprintf(b, sizeof(b), "%ld", axis_long("pan", "max_steps") / 2);
				ok |= axis_write("pan", "target", b);
				snprintf(b, sizeof(b), "%ld", axis_long("tilt", "max_steps") / 2);
				ok |= axis_write("tilt", "target", b);
			} else {
				return -1;
			}
		} else if (!strcmp(path, "/ir") && !strcmp(kv, "mode")) {
			if (!strcmp(v, "auto"))
				ir_cfg = IR_AUTO;
			else if (!strcmp(v, "day"))
				ir_cfg = IR_DAY;
			else if (!strcmp(v, "night"))
				ir_cfg = IR_NIGHT;
			else
				return -1;
			if (ir_cfg != IR_AUTO)
				set_night(ir_cfg == IR_NIGHT, "manual");
			else
				cond_since = 0;
		} else {
			return -1;
		}
	}
	return ok;
}

static void reply_page(struct client *c)
{
	static char buf[PAGE_MAX];
	int fd = open(PAGE_FILE, O_RDONLY);
	ssize_t n = fd >= 0 ? read(fd, buf, sizeof(buf)) : -1;

	if (fd >= 0)
		close(fd);
	if (n > 0)
		reply(c, "200 OK", "text/html; charset=utf-8", buf, n);
	else
		reply(c, "200 OK", "text/html; charset=utf-8", page_min, sizeof(page_min) - 1);
}

static void client_request(struct client *c)
{
	char hdr[256], path[256] = "", *query;
	int n;

	if (sscanf(c->req, "GET %255s", path) != 1) {
		reply(c, "400 Bad Request", "text/plain", "bad request\n", 12);
		return;
	}
	query = strchr(path, '?');
	if (query)
		*query++ = 0;
	if (!strcmp(path, "/stream")) {
		n = snprintf(hdr, sizeof(hdr), "HTTP/1.0 200 OK\r\n"
			     "Cache-Control: no-cache\r\nConnection: close\r\n"
			     "Content-Type: multipart/x-mixed-replace;boundary="
			     BOUNDARY "\r\n\r\n");
		client_set(c, hdr, n, NULL, 0, NULL, 0);
		c->state = C_STREAM;
	} else if (!strcmp(path, "/snapshot.jpg") || !strcmp(path, "/snapshot")) {
		c->state = C_SNAP;		/* answered with the next frame */
	} else if (!strcmp(path, "/")) {
		reply_page(c);
	} else if (!strcmp(path, "/status")) {
		reply_status(c);
	} else if (!strcmp(path, "/ptz") || !strcmp(path, "/ir")) {
		if (query && do_query(path, query) == 0)
			reply_status(c);
		else
			reply(c, "400 Bad Request", "text/plain", "bad request\n", 12);
	} else {
		reply(c, "404 Not Found", "text/plain", "not found\n", 10);
	}
}

static void client_read(struct client *c)
{
	ssize_t r = recv(c->fd, c->req + c->reqlen, REQ_MAX - 1 - c->reqlen, 0);

	if (r <= 0) {
		c->state = C_CLOSE;
		return;
	}
	if (c->state != C_REQ)
		return;			/* ignore anything after the request */
	c->reqlen += r;
	c->req[c->reqlen] = 0;
	if (strstr(c->req, "\r\n\r\n") || strstr(c->req, "\n\n"))
		client_request(c);
	else if (c->reqlen >= REQ_MAX - 1)
		c->state = C_CLOSE;
}

/* A new frame: hand it to every client that is ready for it */
static void frame_out(const void *jpg, size_t len)
{
	char hdr[160];
	int i, n;
	double t = now();
	int stream_due = t - last_sent >= 1.0 / fps - 0.005;

	for (i = 0; i < MAX_CLIENTS; i++) {
		struct client *c = &clients[i];

		if (c->outlen)			/* still sending the previous one */
			continue;
		if (c->state == C_STREAM && stream_due) {
			n = snprintf(hdr, sizeof(hdr), "--" BOUNDARY "\r\n"
				     "Content-Type: image/jpeg\r\nContent-Length: %zu\r\n\r\n",
				     len);
			if (client_set(c, hdr, n, jpg, len, "\r\n", 2) < 0)
				c->state = C_CLOSE;
		} else if (c->state == C_SNAP && frames_since_start >= CAM_SETTLE) {
			n = snprintf(hdr, sizeof(hdr), "HTTP/1.0 200 OK\r\n"
				     "Content-Type: image/jpeg\r\nContent-Length: %zu\r\n"
				     "Cache-Control: no-cache\r\nConnection: close\r\n\r\n", len);
			if (client_set(c, hdr, n, jpg, len, NULL, 0) < 0)
				c->state = C_CLOSE;
			else
				c->state = C_DONE;
		}
	}
	if (stream_due)
		last_sent = t;
}

static void cam_read(void)
{
	struct v4l2_buffer buf = { .type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
				   .memory = V4L2_MEMORY_MMAP };

	if (xioctl(vfd, VIDIOC_DQBUF, &buf) < 0)
		return;
	frames_since_start++;
	if (buf.bytesused && !(buf.flags & V4L2_BUF_FLAG_ERROR))
		frame_out(mem[buf.index], buf.bytesused);
	xioctl(vfd, VIDIOC_QBUF, &buf);
}

int main(int argc, char **argv)
{
	struct sockaddr_in sa = { .sin_family = AF_INET };
	int port = 8080, lfd, opt, one = 1;

	while ((opt = getopt(argc, argv, "p:r:q:d:i:N:D:")) != -1) {
		switch (opt) {
		case 'p': port = atoi(optarg); break;
		case 'r': fps = atof(optarg); break;
		case 'q': quality = atoi(optarg); break;
		case 'd': dev = optarg; break;
		case 'i':
			ir_cfg = !strcmp(optarg, "day") ? IR_DAY :
				 !strcmp(optarg, "night") ? IR_NIGHT : IR_AUTO;
			break;
		case 'N': night_thr = atol(optarg); break;
		case 'D': day_thr = atol(optarg); break;
		default:
			fprintf(stderr, "usage: cam-http [-p port] [-r fps] [-q quality] [-d dev]\n"
				"                [-i auto|day|night] [-N night] [-D day]\n");
			return 2;
		}
	}
	if (fps <= 0 || fps > 30)
		fps = 30;
	signal(SIGPIPE, SIG_IGN);

	lfd = socket(AF_INET, SOCK_STREAM, 0);
	setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
	sa.sin_port = htons(port);
	if (lfd < 0 || bind(lfd, (struct sockaddr *)&sa, sizeof(sa)) < 0 ||
	    listen(lfd, 8) < 0) {
		perror("cam-http: listen");
		return 1;
	}
	fprintf(stderr, "cam-http: port %d, %.1f fps, quality %d, day/night %s (%ld/%ld)\n",
		port, fps, quality, ir_names[ir_cfg], night_thr, day_thr);
	sensor_open();
	set_night(ir_cfg == IR_NIGHT, "start");

	for (;;) {
		struct pollfd pfd[MAX_CLIENTS + 2];
		int map[MAX_CLIENTS + 2], np = 0, i, active = 0;
		static double last_tick;

		if (vfd >= 0 && now() - last_tick >= 1.0) {
			last_tick = now();
			daynight_tick();
		}

		/* finish clients, decide whether the camera is needed */
		for (i = 0; i < MAX_CLIENTS; i++) {
			struct client *c = &clients[i];

			if (c->state == C_DONE && !c->outlen)
				c->state = C_CLOSE;
			if (c->state == C_CLOSE)
				client_close(c);
			if (c->state == C_STREAM || c->state == C_SNAP)
				active = 1;
		}
		if (active) {
			idle_since = 0;
			if (vfd < 0 && cam_start() < 0) {
				for (i = 0; i < MAX_CLIENTS; i++)
					if (clients[i].state == C_STREAM || clients[i].state == C_SNAP)
						clients[i].state = C_CLOSE;
			}
		} else if (vfd >= 0) {
			if (!idle_since)
				idle_since = now();
			else if (now() - idle_since > CAM_IDLE_S)
				cam_stop();
		}

		pfd[np].fd = lfd;
		pfd[np].events = POLLIN;
		map[np++] = -1;
		if (vfd >= 0) {
			pfd[np].fd = vfd;
			pfd[np].events = POLLIN;
			map[np++] = -2;
		}
		for (i = 0; i < MAX_CLIENTS; i++) {
			struct client *c = &clients[i];

			if (c->state == C_FREE)
				continue;
			pfd[np].fd = c->fd;
			pfd[np].events = POLLIN | (c->outlen ? POLLOUT : 0);
			map[np++] = i;
		}

		if (poll(pfd, np, 1000) < 0 && errno != EINTR)
			break;

		for (i = 0; i < np; i++) {
			if (!pfd[i].revents)
				continue;
			if (map[i] == -1) {
				int fd = accept(lfd, NULL, NULL), k;

				if (fd < 0)
					continue;
				for (k = 0; k < MAX_CLIENTS && clients[k].state != C_FREE; k++)
					;
				if (k == MAX_CLIENTS) {
					close(fd);
					continue;
				}
				setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
				memset(&clients[k], 0, sizeof(clients[k]));
				clients[k].fd = fd;
				clients[k].state = C_REQ;
			} else if (map[i] == -2) {
				cam_read();
			} else {
				struct client *c = &clients[map[i]];

				if (pfd[i].revents & (POLLERR | POLLHUP))
					c->state = C_CLOSE;
				else if (pfd[i].revents & POLLIN)
					client_read(c);
			}
		}
		/* send what is pending (also the output prepared above) */
		for (i = 0; i < MAX_CLIENTS; i++)
			if (clients[i].state != C_FREE && clients[i].state != C_CLOSE &&
			    clients[i].outlen)
				client_flush(&clients[i]);
	}

	cam_stop();
	return 0;
}
