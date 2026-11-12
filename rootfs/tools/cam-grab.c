// SPDX-License-Identifier: GPL-2.0
/*
 * cam-grab: minimal V4L2 capture test for the fh8626-camera driver.
 *
 *   cam-grab [-d /dev/video0] [-s /dev/v4l-subdev0] [-a 0|1] [-e EXPOSURE]
 *            [-g GAIN] [-w 0|1] [-R RED] [-B BLUE] [-f nv12|mjpeg]
 *            [-q QUALITY] [-n FRAMES] [-b BUFFERS] [-o FILE]
 *
 * -a 1 switches auto exposure on, -a 0 off; -e/-g (sensor exposure in lines,
 * analogue gain with 32 = 1x) switch it off unless -a 1 is given. -w
 * switches auto white balance on (1) or off (0); -R/-B set the red/blue
 * gains (0x200 = 1.0, decimal or 0x...) with AWB off, then -w 1 (if
 * given) switches it on again from there. -f selects the format, -q the
 * JPEG quality (1..100).
 *
 * Streams FRAMES frames with BUFFERS mmap buffers and prints once per second
 * the sensor exposure and gain and the red/blue balance (0x200 = 1.0), for
 * NV12 also the mean luma and the mean Cb/Cr of mid-luma points (x10,
 * signed), for MJPEG the frame size; then frame rate and sequence gaps.
 * Writes the last frame (raw NV12 or JPEG) to FILE; prints the Y range/mean
 * and mean |U-128|, |V-128| (NV12) or the mean frame size and bit rate
 * (MJPEG).
 */

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <linux/videodev2.h>

#define MAX_BUFS 8

static int xioctl(int fd, unsigned long req, void *arg)
{
	int r;

	do
		r = ioctl(fd, req, arg);
	while (r < 0 && errno == EINTR);
	return r;
}

static int get_ctrl(const char *dev, unsigned int id)
{
	struct v4l2_control c = { .id = id };
	int fd = open(dev, O_RDWR);

	if (fd < 0 || xioctl(fd, VIDIOC_G_CTRL, &c) < 0)
		c.value = -1;
	if (fd >= 0)
		close(fd);
	return c.value;
}

static void trace(int frame, const unsigned char *p, unsigned int w,
		  unsigned int h, const char *dev, const char *sdev)
{
	long sum = 0, n = 0, cb = 0, cr = 0, m = 0;
	unsigned int x, y;

	for (y = h / 36; y < h; y += h / 18)
		for (x = w / 64; x < w; x += w / 32, n++) {
			const unsigned char *c = p + w * h + (y / 2) * w + (x & ~1u);
			unsigned int l = p[y * w + x];

			sum += l;
			if (l >= 40 && l <= 220) {
				cb += c[0] - 128;
				cr += c[1] - 128;
				m++;
			}
		}
	printf("%3d: mean Y %3ld, Cb %+4ld Cr %+4ld, exposure %4d, gain %4d, "
	       "red 0x%03x blue 0x%03x\n", frame, sum / n,
	       m ? cb * 10 / m : 0, m ? cr * 10 / m : 0,
	       get_ctrl(sdev, V4L2_CID_EXPOSURE),
	       get_ctrl(sdev, V4L2_CID_ANALOGUE_GAIN),
	       get_ctrl(dev, V4L2_CID_RED_BALANCE),
	       get_ctrl(dev, V4L2_CID_BLUE_BALANCE));
}

static void set_ctrl(const char *dev, unsigned int id, const char *name, int val)
{
	struct v4l2_control c = { .id = id, .value = val };
	int fd = open(dev, O_RDWR);

	if (fd < 0 || xioctl(fd, VIDIOC_S_CTRL, &c) < 0)
		fprintf(stderr, "cam-grab: %s %s=%d: %s\n", dev, name, val, strerror(errno));
	else
		printf("%s: %s %d\n", dev, name, val);
	if (fd >= 0)
		close(fd);
}

static void stats(const unsigned char *p, unsigned int w, unsigned int h)
{
	unsigned long ysum = 0, usum = 0, vsum = 0, n = 0, m = 0;
	unsigned int x, y, ymin = 255, ymax = 0;

	for (y = 0; y < h; y += 4)
		for (x = 0; x < w; x += 4) {
			unsigned int v = p[y * w + x];

			ysum += v;
			ymin = v < ymin ? v : ymin;
			ymax = v > ymax ? v : ymax;
			n++;
		}
	for (y = 0; y < h / 2; y += 4)
		for (x = 0; x < w; x += 8) {
			const unsigned char *c = p + w * h + y * w + x;

			usum += abs(c[0] - 128);
			vsum += abs(c[1] - 128);
			m++;
		}
	printf("frame: Y %u-%u mean %lu, |U-128| %.1f |V-128| %.1f\n", ymin, ymax,
	       ysum / n, (double)usum / m, (double)vsum / m);
}

int main(int argc, char **argv)
{
	const char *dev = "/dev/video0", *sdev = "/dev/v4l-subdev0", *out = NULL;
	int exposure = -1, gain = -1, frames = 30, nbufs = 4, aec = -1, awb = -1;
	int red = -1, blue = -1, quality = -1, mjpeg = 0;
	unsigned long long bytes = 0;
	unsigned int lastlen = 0;
	struct v4l2_requestbuffers req = { 0 };
	struct v4l2_format fmt = { 0 };
	struct v4l2_buffer buf;
	void *mem[MAX_BUFS];
	size_t len[MAX_BUFS];
	enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
	unsigned long long t0 = 0, t1 = 0;
	unsigned int seq_first = 0, seq_last = 0, i;
	int fd, opt, got = 0, last = -1;

	while ((opt = getopt(argc, argv, "d:s:a:e:g:w:R:B:f:q:n:b:o:")) != -1) {
		switch (opt) {
		case 'd': dev = optarg; break;
		case 's': sdev = optarg; break;
		case 'a': aec = atoi(optarg); break;
		case 'w': awb = atoi(optarg); break;
		case 'f': mjpeg = !strcmp(optarg, "mjpeg"); break;
		case 'q': quality = atoi(optarg); break;
		case 'R': red = strtol(optarg, NULL, 0); break;
		case 'B': blue = strtol(optarg, NULL, 0); break;
		case 'e': exposure = atoi(optarg); break;
		case 'g': gain = atoi(optarg); break;
		case 'n': frames = atoi(optarg); break;
		case 'b': nbufs = atoi(optarg); break;
		case 'o': out = optarg; break;
		default:
			fprintf(stderr, "usage: cam-grab [-d dev] [-s subdev] [-a 0|1] "
				"[-e exposure] [-g gain] [-w 0|1] [-R red] [-B blue] "
				"[-f nv12|mjpeg] [-q quality] [-n frames] [-b buffers] "
				"[-o file]\n");
			return 2;
		}
	}
	if (nbufs < 2 || nbufs > MAX_BUFS || frames < 1)
		return 2;

	if (aec < 0 && (exposure >= 0 || gain >= 0))
		aec = 0;
	if (aec >= 0) {
		set_ctrl(dev, V4L2_CID_EXPOSURE_AUTO, "exposure_auto",
			 aec ? V4L2_EXPOSURE_AUTO : V4L2_EXPOSURE_MANUAL);
		printf("auto exposure %s\n", aec ? "on" : "off");
	}
	if (red >= 0 || blue >= 0) {
		set_ctrl(dev, V4L2_CID_AUTO_WHITE_BALANCE, "white_balance_automatic", 0);
		if (red >= 0)
			set_ctrl(dev, V4L2_CID_RED_BALANCE, "red_balance", red);
		if (blue >= 0)
			set_ctrl(dev, V4L2_CID_BLUE_BALANCE, "blue_balance", blue);
	}
	if (awb >= 0)
		set_ctrl(dev, V4L2_CID_AUTO_WHITE_BALANCE, "white_balance_automatic",
			 !!awb);
	if (quality > 0)
		set_ctrl(dev, V4L2_CID_JPEG_COMPRESSION_QUALITY, "jpeg_quality", quality);
	if (exposure >= 0)
		set_ctrl(sdev, V4L2_CID_EXPOSURE, "exposure", exposure);
	if (gain >= 0)
		set_ctrl(sdev, V4L2_CID_ANALOGUE_GAIN, "analogue_gain", gain);

	fd = open(dev, O_RDWR);
	if (fd < 0) {
		perror(dev);
		return 1;
	}

	fmt.type = type;
	if (xioctl(fd, VIDIOC_G_FMT, &fmt) < 0) {
		perror("VIDIOC_G_FMT");
		return 1;
	}
	fmt.fmt.pix.pixelformat = mjpeg ? V4L2_PIX_FMT_MJPEG : V4L2_PIX_FMT_NV12;
	if (xioctl(fd, VIDIOC_S_FMT, &fmt) < 0) {
		perror("VIDIOC_S_FMT");
		return 1;
	}
	printf("format: %ux%u %.4s, %u bytes\n", fmt.fmt.pix.width, fmt.fmt.pix.height,
	       (char *)&fmt.fmt.pix.pixelformat, fmt.fmt.pix.sizeimage);

	req.count = nbufs;
	req.type = type;
	req.memory = V4L2_MEMORY_MMAP;
	if (xioctl(fd, VIDIOC_REQBUFS, &req) < 0) {
		perror("VIDIOC_REQBUFS");
		return 1;
	}
	for (i = 0; i < req.count; i++) {
		memset(&buf, 0, sizeof(buf));
		buf.type = type;
		buf.memory = V4L2_MEMORY_MMAP;
		buf.index = i;
		if (xioctl(fd, VIDIOC_QUERYBUF, &buf) < 0) {
			perror("VIDIOC_QUERYBUF");
			return 1;
		}
		len[i] = buf.length;
		mem[i] = mmap(NULL, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd,
			      buf.m.offset);
		if (mem[i] == MAP_FAILED) {
			perror("mmap");
			return 1;
		}
		memset(mem[i], 0, buf.length);
		if (xioctl(fd, VIDIOC_QBUF, &buf) < 0) {
			perror("VIDIOC_QBUF");
			return 1;
		}
	}
	printf("buffers: %u\n", req.count);

	if (xioctl(fd, VIDIOC_STREAMON, &type) < 0) {
		perror("VIDIOC_STREAMON");
		return 1;
	}

	while (got < frames) {
		struct pollfd pfd = { .fd = fd, .events = POLLIN };
		unsigned long long ts;

		if (poll(&pfd, 1, 2000) <= 0) {
			fprintf(stderr, "cam-grab: no frame within 2 s (%d received)\n", got);
			break;
		}
		memset(&buf, 0, sizeof(buf));
		buf.type = type;
		buf.memory = V4L2_MEMORY_MMAP;
		if (xioctl(fd, VIDIOC_DQBUF, &buf) < 0) {
			perror("VIDIOC_DQBUF");
			break;
		}
		ts = buf.timestamp.tv_sec * 1000000ULL + buf.timestamp.tv_usec;
		if (!got) {
			t0 = ts;
			seq_first = buf.sequence;
		}
		t1 = ts;
		seq_last = buf.sequence;
		got++;
		last = buf.index;
		lastlen = buf.bytesused;
		bytes += buf.bytesused;
		if (got % 30 == 1 && !mjpeg)
			trace(got - 1, mem[buf.index], fmt.fmt.pix.width,
			      fmt.fmt.pix.height, dev, sdev);
		if (got % 30 == 1 && mjpeg)
			printf("%3d: %u bytes, exposure %4d, gain %4d, red 0x%03x blue 0x%03x\n",
			       got - 1, buf.bytesused, get_ctrl(sdev, V4L2_CID_EXPOSURE),
			       get_ctrl(sdev, V4L2_CID_ANALOGUE_GAIN),
			       get_ctrl(dev, V4L2_CID_RED_BALANCE),
			       get_ctrl(dev, V4L2_CID_BLUE_BALANCE));
		if (got < frames && xioctl(fd, VIDIOC_QBUF, &buf) < 0) {
			perror("VIDIOC_QBUF");
			break;
		}
	}

	if (got > 1)
		printf("frames: %d, sequence %u..%u (%u dropped), %.2f fps\n", got,
		       seq_first, seq_last, seq_last - seq_first + 1 - got,
		       (got - 1) * 1e6 / (double)(t1 - t0));
	else
		printf("frames: %d\n", got);

	if (last >= 0) {
		if (mjpeg)
			printf("jpeg: mean %llu bytes per frame, %.1f Mbit/s\n", bytes / got,
			       got > 1 ? (bytes - lastlen) * 8.0 / (t1 - t0) : 0);
		else
			stats(mem[last], fmt.fmt.pix.width, fmt.fmt.pix.height);
		if (out) {
			FILE *f = fopen(out, "wb");
			unsigned int n = mjpeg ? lastlen : fmt.fmt.pix.sizeimage;

			if (!f || fwrite(mem[last], 1, n, f) != n)
				perror(out);
			else
				printf("written: %s\n", out);
			if (f)
				fclose(f);
		}
	}

	xioctl(fd, VIDIOC_STREAMOFF, &type);
	for (i = 0; i < req.count; i++)
		munmap(mem[i], len[i]);
	close(fd);

	return got == frames ? 0 : 1;
}
