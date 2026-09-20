/*
 * Mideo 共享内存输入：元数据走 pipe，BGRA 像素只在本进程映射。
 * 色彩转换直接写入编码输入 YUV；转换完成后才确认消费，不保留源槽引用。
 * 与 FFmpeg 同许可：LGPL-2.1-or-later。
 */
/* 在系统头之前声明 POSIX.1-2008，以暴露 O_CLOEXEC。 */
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#include "config.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include "libavutil/imgutils.h"
#include "libavutil/opt.h"
#include "libswscale/swscale.h"
#include "avformat.h"
#include "demux.h"
#include "internal.h"

typedef struct MideoContext {
    const AVClass *class;
    char *buffer_path;
    int width, height, ack_fd, slots;
    AVRational framerate;
    uint8_t *mapping;
    size_t mapping_size, frame_bytes;
    struct SwsContext *scale;
    int64_t frame;
    uint64_t next_ticket;
} MideoContext;

static int mideo_header(AVFormatContext *ctx)
{
    MideoContext *s = ctx->priv_data;
    struct stat info;
    int fd, result;
    AVStream *stream;
    if (!s->buffer_path || s->framerate.num <= 0 || s->framerate.den <= 0)
        return AVERROR(EINVAL);
    result = av_image_get_buffer_size(AV_PIX_FMT_BGRA, s->width, s->height, 1);
    if (result <= 0) return AVERROR(EINVAL);
    s->frame_bytes = result;
    fd = open(s->buffer_path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return AVERROR(errno);
    if (fstat(fd, &info) || info.st_size < (uint64_t)s->frame_bytes * s->slots) {
        close(fd);
        return AVERROR(EINVAL);
    }
    s->mapping_size = info.st_size;
    s->mapping = mmap(NULL, s->mapping_size, PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (s->mapping == MAP_FAILED) {
        s->mapping = NULL;
        return AVERROR(errno);
    }
    s->scale = sws_getContext(s->width, s->height, AV_PIX_FMT_BGRA,
                              s->width, s->height, AV_PIX_FMT_YUV420P,
                              SWS_BICUBIC, NULL, NULL, NULL);
    if (!s->scale) return AVERROR(ENOMEM);
    stream = avformat_new_stream(ctx, NULL);
    if (!stream) return AVERROR(ENOMEM);
    stream->codecpar->codec_type = AVMEDIA_TYPE_VIDEO;
    stream->codecpar->codec_id = AV_CODEC_ID_RAWVIDEO;
    stream->codecpar->format = AV_PIX_FMT_YUV420P;
    stream->codecpar->width = s->width;
    stream->codecpar->height = s->height;
    stream->avg_frame_rate = s->framerate;
    avpriv_set_pts_info(stream, 64, s->framerate.den, s->framerate.num);
    return 0;
}

static int mideo_packet(AVFormatContext *ctx, AVPacket *packet)
{
    MideoContext *s = ctx->priv_data;
    char line[96], trailing;
    int used = 0, slot, ret, output_stride[4];
    uint64_t ticket;
    uint8_t *output[4];
    const uint8_t *input[4] = { NULL };
    int input_stride[4] = { s->width * 4, 0, 0, 0 };
    while (used < sizeof(line) - 1) {
        int byte = avio_r8(ctx->pb);
        if (avio_feof(ctx->pb)) return used ? AVERROR_INVALIDDATA : AVERROR_EOF;
        if (byte == '\n') break;
        line[used++] = byte;
    }
    if (used == sizeof(line) - 1) return AVERROR_INVALIDDATA;
    line[used] = 0;
    if (sscanf(line, "%d %" SCNu64 "%c", &slot, &ticket, &trailing) != 2 ||
        slot < 0 || slot >= s->slots || ticket != s->next_ticket++)
        return AVERROR_INVALIDDATA;
    ret = av_image_get_buffer_size(AV_PIX_FMT_YUV420P, s->width, s->height, 1);
    if (ret < 0) return ret;
    ret = av_new_packet(packet, ret);
    if (ret < 0) return ret;
    ret = av_image_fill_arrays(output, output_stride, packet->data,
                               AV_PIX_FMT_YUV420P, s->width, s->height, 1);
    if (ret < 0) return ret;
    input[0] = s->mapping + (size_t)slot * s->frame_bytes;
    ret = sws_scale(s->scale, input, input_stride, 0, s->height, output, output_stride);
    if (ret != s->height) return AVERROR_INVALIDDATA;
    packet->pts = packet->dts = s->frame++;
    packet->duration = 1;
    packet->stream_index = 0;
    // 此时只剩独立 YUV 编码输入；源 BGRA 槽可以安全复用。
    used = snprintf(line, sizeof(line), "%" PRIu64 "\n", ticket);
    do { ret = write(s->ack_fd, line, used); } while (ret < 0 && errno == EINTR);
    if (ret != used) return AVERROR(EIO);
    return 0;
}

static int mideo_close(AVFormatContext *ctx)
{
    MideoContext *s = ctx->priv_data;
    sws_freeContext(s->scale);
    if (s->mapping) munmap(s->mapping, s->mapping_size);
    return 0;
}
#define OFFSET(x) offsetof(MideoContext, x)
#define DEC AV_OPT_FLAG_DECODING_PARAM
static const AVOption options[] = {
    { "buffer_path", "共享像素文件", OFFSET(buffer_path), AV_OPT_TYPE_STRING, {.str = NULL}, 0, 0, DEC },
    { "video_size", "固定画幅", OFFSET(width), AV_OPT_TYPE_IMAGE_SIZE, {.str = NULL}, 0, 0, DEC },
    { "framerate", "确定性帧率", OFFSET(framerate), AV_OPT_TYPE_VIDEO_RATE, {.str = "30"}, 0, INT_MAX, DEC },
    { "ack_fd", "消费确认描述符", OFFSET(ack_fd), AV_OPT_TYPE_INT, {.i64 = 1}, 1, INT_MAX, DEC },
    { "slots", "槽数", OFFSET(slots), AV_OPT_TYPE_INT, {.i64 = 3}, 1, 16, DEC },
    { NULL }
};
static const AVClass mideo_class = {
    .class_name = "mideo shared buffer", .item_name = av_default_item_name,
    .option = options, .version = LIBAVUTIL_VERSION_INT,
};
const FFInputFormat ff_mideoshm_demuxer = {
    .p.name = "mideoshm", .p.long_name = "Mideo shared BGRA frames",
    .p.priv_class = &mideo_class,
    .priv_data_size = sizeof(MideoContext),
    .flags_internal = FF_INFMT_FLAG_INIT_CLEANUP,
    .read_header = mideo_header, .read_packet = mideo_packet, .read_close = mideo_close,
    .raw_codec_id = AV_CODEC_ID_RAWVIDEO,
};
