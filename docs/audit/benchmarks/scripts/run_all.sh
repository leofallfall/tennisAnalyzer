set -u
cd /home/user/tennis-performance-analyzer
export UV_SYSTEM_CERTS=1 GLOG_minloglevel=3; unset UV_NATIVE_TLS
B=/tmp/claude-0/bench; R=$B/results.jsonl; : > $R
P="uv run python /tmp/claude-0/benchscripts/pipeline_bench.py"
$P $B/src_1080p60.mp4 $B/out4 1 1 2>/dev/null | grep '^{' >> $R
$P $B/src_1080p60.mp4 $B/out4s2 2 0 2>/dev/null | grep '^{' >> $R
taskset -c 0 $P $B/src_1080p60.mp4 $B/out1 1 1 2>/dev/null | grep '^{' >> $R
taskset -c 0,1 $P $B/src_1080p60.mp4 $B/out2 1 1 2>/dev/null | grep '^{' >> $R
cd $B
for spec in "decode_h264_4c|ffmpeg -v error -i src_1080p60.mp4 -f null -" \
 "decode_h264_1c|taskset -c 0 ffmpeg -v error -threads 1 -i src_1080p60.mp4 -f null -" \
 "decode_hevc20s_4c|ffmpeg -v error -i src_hevc_1080p60_20s.mov -f null -" \
 "decode_hevc20s_1c|taskset -c 0 ffmpeg -v error -threads 1 -i src_hevc_1080p60_20s.mov -f null -" \
 "proxy_current_4c|ffmpeg -y -v error -i src_1080p60.mp4 -map 0:v:0 -an -vf scale=trunc(iw/2)*2:trunc(ih/2)*2 -fps_mode cfr -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p -movflags +faststart p1.mp4" \
 "proxy_current_1c|taskset -c 0 ffmpeg -y -v error -threads 1 -i src_1080p60.mp4 -map 0:v:0 -an -vf scale=trunc(iw/2)*2:trunc(ih/2)*2 -fps_mode cfr -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p -movflags +faststart p2.mp4" \
 "proxy_720p_crf23_4c|ffmpeg -y -v error -i src_1080p60.mp4 -an -vf scale=-2:720 -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -movflags +faststart p3.mp4" \
 "proxy_720p_crf23_1c|taskset -c 0 ffmpeg -y -v error -threads 1 -i src_1080p60.mp4 -an -vf scale=-2:720 -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -movflags +faststart p4.mp4" \
 "scan_10fps_256w_1c|taskset -c 0 ffmpeg -v error -threads 1 -skip_frame nokey -i src_1080p60.mp4 -vf fps=10,scale=256:-2 -f null -" \
 "scan_10fps_256w_full_decode_1c|taskset -c 0 ffmpeg -v error -threads 1 -i src_1080p60.mp4 -vf fps=10,scale=256:-2 -f null -" ; do
  name=${spec%%|*}; cmd=${spec#*|}
  echo "{\"stage\":\"$name\",\"res\":$(python3 runcmd.py $cmd)}" >> $R
done
ls -la p1.mp4 p3.mp4 >> $B/sizes.txt
echo DONE >> $R
