# PersonVision AI — Ứng Dụng Phát Hiện Người Trong Video (Windows Portable)

Ứng dụng desktop giúp quét hàng loạt video trong một thư mục để **tự động phát hiện sự xuất hiện của con người**, **ghi nhận chính xác mốc thời gian**, **chụp lại hình ảnh (toàn khung + cận cảnh)**, **lưu lịch sử vĩnh viễn**, và **bật phát lại video ngay tại thời điểm phát hiện**.

Ứng dụng chạy hoàn toàn **Offline** trên CPU (không cần GPU NVIDIA, không cần Internet), hỗ trợ đóng gói thành bản **Portable** để copy sang bất kỳ máy Windows 10/11 nào và chạy ngay.

---

## Tính năng nổi bật

- **Nhận diện người Offline bằng AI (`YOLO11n ONNX` + `ONNXRuntime`):**
  - Tích hợp sẵn mô hình `yolo11n.onnx` (~10.2 MB) chạy trực tiếp trên CPU với độ chính xác cao, phân biệt người với các vật thể khác.
- **Quét toàn bộ thư mục video:**
  - Chọn thư mục qua hộp thoại Windows, tùy chọn quét cả thư mục con bên trong.
  - Hỗ trợ đa định dạng: `.mp4`, `.avi`, `.mov`, `.mkv`, `.wmv`, `.flv`, `.webm`, `.m4v`, `.ts`, `.mpg`, `.mpeg`, `.3gp` (hỗ trợ đầy đủ tên thư mục/file tiếng Việt có dấu).
  - Tùy chỉnh **tần suất kiểm tra khung hình** (`0.5s`, `1s`, `2s`, `5s`), **độ tin cậy tối thiểu** (`25% – 85%`) và **thời gian gộp khoảnh khắc liên tiếp** để tránh chụp lặp quá nhiều ảnh giống nhau.
  - Hỗ trợ **Tạm dừng / Tiếp tục / Dừng giữa chừng** (các kết quả đã quét trước khi dừng vẫn được lưu lại).
- **Chống quét trùng thông minh:**
  - Mặc định tự động nhận biết và **không quét lại các video đã có tên trong lịch sử quét** (`status = done`), đồng thời hiển thị thông báo rõ số lượng video đã được quét trước đó và số video mới sẽ quét.
  - Bấm lại nút **Quét Video** ở thanh menu sẽ tự động làm mới màn hình về trạng thái ban đầu khi chưa quét (nếu không có tác vụ nào đang chạy ngầm).
- **Chụp ảnh & Lưu trữ lịch sử Portable:**
  - Mỗi khoảnh khắc phát hiện lưu đồng thời **ảnh toàn khung hình** (vẽ khung đánh dấu màu xanh, độ tin cậy, số người và mốc thời gian) và **ảnh cận cảnh** người có độ tin cậy cao nhất.
  - Dữ liệu lịch sử (`history.db` SQLite + thư mục `snapshots/`) được lưu trong thư mục `data/` ngay cạnh file chạy `.exe` (hoặc `%LOCALAPPDATA%\VideoPersonDetector\data` nếu thư mục cài đặt chỉ đọc). Khi copy ứng dụng sang máy khác, toàn bộ lịch sử và ảnh chụp sẽ đi theo.
- **Trình phát video tích hợp sẵn (nhảy thẳng đến thời điểm phát hiện):**
  - Duyệt lịch sử theo **Video** (phân tách rõ video có người / không có người / lỗi), **Thư viện ảnh**, hoặc **Các lần quét**.
  - Bấm vào ảnh hoặc nút **"Xem từ MM:SS"** để mở trình phát và nhảy ngay tới giây phút có người xuất hiện.
  - Thanh timeline hiển thị trực quan mọi mốc thời gian có người trên video, kèm nút **Điểm trước / Điểm sau**, tua `±5s`, chỉnh tốc độ (`0.5x – 2.0x`), mở thư mục hoặc mở bằng trình phát ngoài.
  - Tự động phát trực tiếp H.264 `.mp4`/`.webm` (qua HTTP `206 Partial Content`), đồng thời tự động chuyển đổi định dạng `.avi`/`.mkv`/H.265 sang H.264 cache bằng `ffmpeg` đóng gói kèm để xem mượt ngay trong ứng dụng.
  - Hỗ trợ nút **"Chọn lại file video..."** khi chuyển sang máy khác làm thay đổi ổ đĩa hoặc đường dẫn file gốc (tự động ánh xạ luôn các video cùng thư mục).

---

## Hướng dẫn sử dụng (Bản đóng gói Portable)

### 1. Chạy ứng dụng trên máy hiện tại hoặc máy Windows khác
1. Tải file `PersonVisionAI_Portable.zip` (từ mục **Releases** trên GitHub hoặc thư mục `dist/`) và giải nén ra một thư mục bất kỳ (ví dụ `D:\Apps\PersonVisionAI`).
2. Mở thư mục vừa giải nén và nhấp đúp **`PersonVisionAI.exe`**.
   - *Không cần cài đặt Python, OpenCV, FFmpeg hay bất kỳ thư viện nào khác trên máy đích.*
   - *Lưu ý lần đầu:* Nếu Windows Defender SmartScreen hiện cảnh báo do file tự đóng gói chưa ký chứng chỉ số, nhấn **More info** → **Run anyway**.

### 2. Quy trình quét và xem kết quả
1. **Bước 1 — Chọn thư mục video:** Tại tab **Quét Video**, bấm **Chọn thư mục...** hoặc dán đường dẫn thư mục chứa video. Hệ thống sẽ đếm trước tổng số video, số video đã quét trước đó và số video mới.
2. **Bước 2 — Cấu hình & Quét:** Điều chỉnh tần suất lấy mẫu (khuyên dùng `1 giây`), ngưỡng tin cậy (`45%`), sau đó bấm **Bắt đầu quét ngay**. Ảnh người phát hiện sẽ xuất hiện trực tiếp trong quá trình quét.
3. **Bước 3 — Xem lịch sử & phát lại:** Chuyển sang tab **Lịch Sử & Kết Quả** để:
   - Lọc theo **lần quét**, tìm kiếm theo **tên video/thư mục**, hoặc bật **Chỉ hiện video có người**.
   - Chọn video bên trái để xem danh sách toàn bộ mốc thời gian xuất hiện người bên phải.
   - Bấm vào ảnh hoặc nút **Xem từ `MM:SS`** để bật phát lại video ngay tại đúng khoảnh khắc đó.

### 3. Mang dữ liệu sang máy tính khác
- Toàn bộ cơ sở dữ liệu lịch sử và ảnh chụp nằm trong thư mục `data/` cạnh `PersonVisionAI.exe`.
- Khi copy cả thư mục `PersonVisionAI` sang USB hoặc máy tính khác, lịch sử và ảnh chụp vẫn còn nguyên.
- Nếu video gốc trên máy mới nằm ở ổ đĩa/thư mục khác, mở video đó trong ứng dụng và bấm **"Chọn lại file video..."** để cập nhật lại vị trí mới.

---

## Kiến trúc hệ thống & Cấu trúc thư mục

```text
VideoPersonDetector/
├── main.py                  # Điểm khởi chạy desktop (Uvicorn + Edge WebView2, có fallback trình duyệt)
├── backend/
│   ├── paths.py             # Định vị tài nguyên đóng gói (_MEIPASS) và thư mục data/ portable
│   ├── database.py          # SQLite WAL lưu phiên quét, danh sách video, mốc phát hiện & cài đặt
│   ├── detector.py          # Bộ nhận diện YOLO11n ONNX (CPU) + vẽ khung & cắt ảnh cận cảnh
│   ├── scanner.py           # Bộ quét thư mục nền, lọc trùng tên lịch sử, gom cụm khoảnh khắc
│   ├── video_service.py     # Streaming HTTP 206 + tự động chuyển đổi AVI/MKV/HEVC qua FFmpeg
│   └── api.py               # REST API FastAPI + hộp thoại chọn thư mục/file gốc của Windows
├── web/
│   ├── index.html           # Giao diện tiếng Việt hiện đại (chạy hoàn toàn offline)
│   ├── styles.css           # Dark Studio theme, responsive grid, thanh timeline đánh dấu
│   └── app.js               # Điều khiển quét, lịch sử, thư viện ảnh và trình phát video
├── models/
│   └── yolo11n.onnx         # Mô hình AI nhận diện người (COCO class 0)
├── assets/
│   └── icon.ico             # Biểu tượng ứng dụng Windows
├── build.spec               # Cấu hình đóng gói PyInstaller (--onedir)
├── build_package.ps1        # Script tự động build thư mục dist/PersonVisionAI và file ZIP
└── requirements.txt         # Danh sách thư viện Python
```

---

## Hướng dẫn chạy từ mã nguồn (Dành cho lập trình viên)

### Yêu cầu môi trường
- Windows 10 / 11 (64-bit)
- Python **3.11** (khuyên dùng, hoặc tạo môi trường bằng `uv`)

### Cài đặt và chạy thử
```powershell
# 1. Tạo môi trường ảo Python 3.11
uv venv --python 3.11 .venv
# Hoặc: py -3.11 -m venv .venv

# 2. Cài đặt thư viện
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
# Hoặc: .\.venv\Scripts\pip install -r requirements.txt

# 3. Khởi chạy ứng dụng
.\.venv\Scripts\python.exe main.py
```

### Tự đóng gói bản Portable `.exe` và `.zip`
Chạy script PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File build_package.ps1
```
Sau khi hoàn tất:
- Thư mục chạy trực tiếp: `dist\PersonVisionAI\PersonVisionAI.exe`
- File nén để chia sẻ/mang sang máy khác: `dist\PersonVisionAI_Portable.zip`

*(Ghi chú: File `PersonVisionAI_Portable.zip` có dung lượng ~147 MB nên được bỏ qua trong `.gitignore` và được phát hành trực tiếp tại mục **Releases** trên GitHub).*

---

## Giải đáp sự cố thường gặp

- **Mở video `.avi` / `.mkv` / `.wmv` lần đầu hơi chậm vài giây:**
  Trình phát web tích hợp chỉ phát trực tiếp chuẩn H.264/WebM; với các định dạng cũ hoặc codec HEVC, ứng dụng tự động dùng FFmpeg tích hợp sẵn chuyển tạm sang H.264 vào `data/cache/`. Các lần bấm xem tiếp theo của video đó sẽ phát tức thì.
- **Máy đích không có Microsoft Edge WebView2:**
  Ứng dụng tự động chuyển sang mở giao diện qua Microsoft Edge/Chrome ở chế độ `--app` (cửa sổ độc lập) hoặc trình duyệt mặc định của hệ thống.
- **Không thấy dữ liệu quét cũ sau khi cài ở `C:\Program Files`:**
  Nếu ứng dụng được đặt trong thư mục không có quyền ghi, dữ liệu sẽ tự chuyển sang lưu tại `%LOCALAPPDATA%\VideoPersonDetector\data`. Khuyên dùng ở thư mục có quyền ghi (như `Desktop`, ổ `D:\` hoặc USB) để giữ chế độ Portable hoàn chỉnh.

---

## Giấy phép
Phát hành theo giấy phép [MIT](LICENSE).
