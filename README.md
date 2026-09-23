# YouTube News Crawler + PyAnnote VAD

Project độc lập để crawl audio tin tức từ YouTube, chạy PyAnnote VAD và xuất WAV segment dài **5-30 giây**. Pipeline dừng theo tổng thời lượng segment hợp lệ, mặc định **200 giờ** và hỗ trợ tối đa **1.000 giờ**.

Nguồn mặc định:

```text
https://www.youtube.com/@daihanoi-htv/videos
```

## Luồng xử lý

```text
YouTube metadata
  -> lọc video tin tức và video quá ngắn
  -> sort video dài trước
  -> yt-dlp tải best audio
  -> FFmpeg chuẩn hóa PCM16, mono, 16 kHz
  -> pyannote/segmentation-3.0 VAD
  -> loại speech <5s
  -> giữ speech 5-30s
  -> chia đều speech >30s thành nhiều đoạn 5-30s
  -> WAV segments + SQLite checkpoint + EDA_result
```

Vùng speech dài được hard-split theo độ dài. Pipeline không tìm khoảng lặng bên trong trước khi cắt, đúng theo yêu cầu hiện tại.

## Yêu cầu

- Python 3.10, 3.11 hoặc 3.12.
- `ffmpeg` và `ffprobe` có trong `PATH`.
- Dung lượng trống tối thiểu khoảng 30 GB cho 200 giờ; nên chuẩn bị 150 GB trở lên nếu chạy 1.000 giờ.
- GPU NVIDIA được khuyến nghị. CPU vẫn chạy được nhưng rất chậm.
- Hugging Face read token đã được cấp quyền cho `pyannote/segmentation-3.0`.

Trước lần chạy đầu tiên:

1. Mở <https://huggingface.co/pyannote/segmentation-3.0> và chấp nhận điều kiện.
2. Tạo read token tại <https://huggingface.co/settings/tokens>.

## Setup trên Linux/HPC

```bash
cd /path/to/Data_Crawl_signet

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

# Cài PyTorch/torchaudio phù hợp CUDA của máy trước nếu cần GPU.
python -m pip install torch torchaudio
python -m pip install -r requirements.txt

ffmpeg -version
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

Nếu máy HPC không cho tạo `venv` hoặc không có Internet, dùng môi trường Python/Conda và wheelhouse nội bộ do quản trị viên cung cấp. Không cần quyền root để chạy pipeline sau khi dependency đã sẵn sàng.

Đặt token cho phiên terminal hiện tại:

```bash
export HF_TOKEN="hf_your_read_token"
export PYANNOTE_METRICS_ENABLED=0
```

Không ghi token vào `config.toml`, notebook hoặc Git.

## Setup trên Windows PowerShell

```powershell
cd C:\Users\ASUS\Downloads\Data_Crawl_signet

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install torch torchaudio
python -m pip install -r requirements.txt

$env:HF_TOKEN = "hf_your_read_token"
$env:PYANNOTE_METRICS_ENABLED = "0"
```

FFmpeg phải có trong `PATH`:

```powershell
ffmpeg -version
ffprobe -version
```

## Kiểm tra môi trường

```bash
python run_pipeline.py doctor
```

Tất cả dòng quan trọng phải là `[OK]`. `doctor` kiểm tra Python, FFmpeg, dependency, token, CUDA và dung lượng đĩa.

## Smoke test một video

```bash
python run_pipeline.py discover --max-videos-per-source 10
python run_pipeline.py run --target-hours 0.1 --max-videos 1
python run_pipeline.py report
```

Kiểm tra:

```text
EDA_result/REPORT.md
EDA_result/segments.csv
data/segments/<video_id>/*.wav
```

## Chạy full 200 giờ

```bash
python run_pipeline.py discover
python run_pipeline.py run --target-hours 200
```

SQLite được checkpoint sau từng video; bộ file `EDA_result` được cập nhật mỗi 10 video và khi kết thúc. Có thể dừng bằng `Ctrl+C` và chạy lại đúng lệnh để resume:

```bash
python run_pipeline.py run --target-hours 200
```

## Mở rộng tới 1.000 giờ

```bash
python run_pipeline.py run --target-hours 1000
```

Nếu tổng thời lượng video được chọn không đủ, pipeline cảnh báo trước. Thêm kênh vào `project.source_urls` hoặc điều chỉnh keyword trong `config.toml`, sau đó chạy lại:

```bash
python run_pipeline.py discover
python run_pipeline.py run --target-hours 1000
```

## Chạy bằng notebook

Mở file:

```text
notebooks/run_pipeline.ipynb
```

Notebook sử dụng `getpass` để nhập token ẩn và gọi cùng CLI với terminal. Vì trạng thái nằm trong `state/pipeline.sqlite3`, notebook và terminal có thể resume lẫn nhau.

## Output

| Đường dẫn | Nội dung |
|---|---|
| `data/segments/<video_id>/*.wav` | Segment PCM16 mono 16 kHz, dài 5-30s |
| `state/pipeline.sqlite3` | Checkpoint video và segment |
| `state/yt_dlp_archive.txt` | Archive tránh tải lặp |
| `EDA_result/segments.csv` | Manifest toàn bộ segment và nguồn YouTube |
| `EDA_result/videos.csv` | Trạng thái từng video |
| `EDA_result/failures.csv` | Video lỗi để retry/debug |
| `EDA_result/summary.json` | Thống kê máy đọc |
| `EDA_result/REPORT.md` | Báo cáo tiến độ dễ đọc |

Mặc định file download nén và full WAV được xóa sau khi segment thành công để tiết kiệm dung lượng. Segment và SQLite không bị xóa. Đổi hai cờ trong `config.toml` nếu cần giữ source:

```toml
[storage]
keep_downloaded_source = true
keep_full_wav = true
```

`EDA_result` được cập nhật mỗi 10 video và khi kết thúc/dừng bằng `Ctrl+C`; SQLite vẫn checkpoint ngay sau từng video. Có thể đổi tần suất:

```toml
[runtime]
report_every_videos = 10
```

## Cấu hình quan trọng

```toml
[project]
target_hours = 200.0
min_video_duration_sec = 300.0
require_news_keyword = true

[audio]
min_segment_sec = 5.0
max_segment_sec = 30.0

[vad]
device = "auto" # auto, cuda, hoặc cpu
min_duration_on = 0.10
min_duration_off = 0.30
```

Video được sort theo thời lượng giảm dần để giảm overhead download/model load. `target_hours` được tính từ tổng duration trong `segments.csv`, không lấy từ duration video gốc.

## Chạy test

Test không tải model và không truy cập YouTube:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## Lưu ý vận hành

- `failures.csv` lưu lỗi tải/VAD. Chạy lại `run` để retry, tối đa theo `max_attempts_per_video`.
- YouTube có thể yêu cầu cookie. Đặt `YTDLP_COOKIE_FILE=/path/to/cookies.txt` nếu tài khoản của bạn có quyền truy cập nội dung đó.
- Pipeline không vượt DRM, video private hoặc quyền truy cập của tài khoản.
- Hãy kiểm tra điều khoản YouTube và quyền sử dụng nội dung trước khi phân phối hoặc dùng dataset để huấn luyện.
