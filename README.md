# AIVN Excel QA

Giải pháp cho đề thi AI2026, Chủ đề 2 "Hỏi đáp trên bảng tính Excel": trả lời câu hỏi ngôn ngữ tự nhiên (tiếng Anh, Việt, Hàn, Trung…) trên các file `.xlsx` có header nhiều tầng, ô gộp, nhiều bảng hoặc nhiều sheet. LLM dùng **Qwen3.7 Flash qua OpenRouter**, đúng yêu cầu của đề.

## Yêu cầu
- Python **3.11** (đã chạy với 3.11.5), hệ điều hành **Linux**. Phần thực thi dùng `multiprocessing` kiểu `fork`, không chạy trên Windows.
- Thư viện đã ghim đúng phiên bản dùng khi đo kết quả: openpyxl 3.1.5, pandas 3.0.2, numpy 1.26.4, openai 2.30.0 (xem `requirements.txt`).
- Một khóa API OpenRouter.

## Cài đặt
```bash
git clone https://github.com/nguyenchiminhhieu/aivn-excel-qa.git
cd aivn-excel-qa
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # rồi điền OPENROUTER_API_KEY vào .env
```
Có thể đặt khóa bằng biến môi trường thay cho `.env`: `export OPENROUTER_API_KEY=...`. Không đưa khóa vào mã nguồn, và không commit file `.env`.

## Chạy
```bash
python run.py --data-dir "<thư mục chứa xlsx_files/ và queries.json>" --out submission.csv
```
Thư mục dữ liệu có cùng cấu trúc với bộ dữ liệu BTC công bố:
```
<data-dir>/
├── xlsx_files/        # các file .xlsx
└── queries.json       # [{"id": ..., "file_name": "...", "query": "..."}]
```
- `file_name` có thể là một tên file hoặc một danh sách tên file (câu hỏi trên nhiều file). Nếu có thêm khóa `file_names` thì khóa này được ưu tiên. Tên file được hiểu tương đối với `xlsx_files/`.
- Kết quả: `submission.csv` (UTF-8) gồm hai cột `id,answer`, mỗi `id` đúng một dòng, theo đúng thứ tự trong `queries.json`. Câu nào không giải được vẫn có dòng, với ô `answer` để trống, nên file luôn hợp lệ.
- Trước khi chạy, chương trình gửi một yêu cầu rất nhỏ để kiểm tra khóa API. Khóa sai thì dừng ngay với thông báo rõ ràng.
- Cuối lượt chạy, nếu có câu trả lời rỗng thì chương trình in cảnh báo. Nếu **mọi** câu đều rỗng thì thoát với mã 2.

Tham số dòng lệnh:

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--data-dir` | (bắt buộc) | Thư mục dữ liệu như trên |
| `--out` | `submission.csv` | File kết quả |
| `--workers` | `6` | Số câu hỏi xử lý song song |
| `--ids` | – | Chỉ chạy các id liệt kê, cách nhau bởi dấu phẩy. Nên dùng kèm `--out` riêng khi debug |
| `--trace` | – | Ghi file JSONL: các ứng viên, đường quyết định, token, độ trễ của từng câu |

Biến môi trường (hoặc `.env`):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `OPENROUTER_API_KEY` | – | Bắt buộc |
| `QA_MODEL` | `qwen/qwen3.7-flash` | Model trên OpenRouter |
| `QA_TIME_BUDGET` | 96 giây × số câu | Ngân sách **mềm** cho cả lượt chạy, tính từ lúc khởi động. Sau 85% ngân sách, câu hỏi không được leo thang sang bước 2 nữa, nhưng lượt chạy vẫn có thể vượt ngân sách |
| `QA_CALL_TIMEOUT` | `90` | Timeout mỗi yêu cầu HTTP (giây), chưa tính các lần retry |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Endpoint |

File `.env` được tìm ở thư mục hiện tại trước, sau đó ở thư mục dự án. Biến môi trường thật luôn được ưu tiên hơn `.env`; dòng trống giá trị bị bỏ qua.

Chấm điểm Exact Match:
```bash
python evaluate.py submission.csv "<data-dir>/sample_submission.csv"   # hoặc answers.json của BTC
```
File đáp án có thể là CSV `id,answer` hoặc JSON dạng `[{"id": ..., "answer": ...}]`. Id được so dưới dạng chuỗi; đáp án được bỏ khoảng trắng đầu/cuối rồi so khớp chính xác.

## Kiến trúc
Sơ đồ: `docs/architecture.drawio` (mở bằng app.diagrams.net hoặc extension Draw.io Integration của VS Code). Trang 1 là luồng xử lý, trang 2 là tech stack.

**Phần A – mỗi workbook, chạy một lần, không gọi LLM** (`excel_parser.py`, `engine/ir.py`)
- Đọc ô bằng openpyxl: lấy giá trị cache của công thức, tự tính khi không có cache (biểu thức được kiểm tra AST theo danh sách trắng trước khi tính); xử lý ô gộp, số/ngày dạng text, ký hiệu thiếu dữ liệu.
- Tách vùng bảng (bảng xếp chồng, bảng cạnh nhau, biểu mẫu key–value) và dựng header nhiều tầng, có ghép các header bị xuống dòng.
- Dựng cây hàng cha › con từ nhiều tín hiệu: thụt lề, cột Indent Level, hàng tiêu đề nhóm, nhãn khoảng lồng nhau, tổng nhất quán (cha = Σ con), khối nhãn lặp lại, nhãn thời gian (năm/quý). Đánh dấu dòng tổng hợp.
- Kết quả là **TableIR**: mỗi giá trị kèm địa chỉ ô A1.

**Phần B – mỗi câu hỏi** (`engine/v3.py`)
- Mỗi bảng được đưa cho chương trình dưới dạng **pandas DataFrame** `T0, T1, …` với các cột `row`, `path`, `level_k`, `is_summary`, `has_children` và tên cột giá trị đọc được, kèm bảng dài `facts`.
- LLM nhận một **bản đồ** của workbook. Mỗi cột trong bản đồ có chú thích ngữ nghĩa: rate, average, count, code hay year; có cộng dồn được không; đơn vị gì. Text đáng ngờ trong ô được đánh dấu.
- **Bước 1** gồm hai ứng viên chạy song song, đều tắt reasoning:
  - Ứng viên thứ nhất luôn là **chương trình pandas** do LLM viết, chạy trong tiến trình con (timeout 25 giây, được sửa 1 lần nếu lỗi).
  - Ứng viên thứ hai là **đọc trực tiếp bản đồ**, khi bản đồ đủ nhỏ (≤ 45.000 ký tự và mọi bảng ≤ 150 hàng). Nếu bản đồ lớn hơn thì ứng viên thứ hai là một chương trình pandas khác, chạy ở nhiệt độ 0.7.
  
  Nếu hai đáp án (sau khi định dạng) trùng nhau và có căn cứ trong dữ liệu thì trả lời luôn.
- **Bước 2** chạy khi bước 1 không cho hai đáp án trùng nhau và có căn cứ: thêm 2 chương trình với reasoning `low` (cộng một lượt đọc trực tiếp với reasoning `low` nếu bản đồ đủ nhỏ). Sau đó bỏ phiếu trên mọi ứng viên hợp lệ của cả hai bước. Hòa phiếu thì gọi trọng tài.
- Nếu vẫn không có đáp án, chương trình thử thêm một lượt đọc trực tiếp với reasoning `low`.
- **Định dạng đáp án**: số chữ số thập phân lấy từ câu hỏi (có regex cho tiếng Anh, Việt, Hàn, Trung, Nhật), không có thì lấy từ đặc tả LLM trả về. Làm tròn half-up bằng `Decimal`.

## Kết quả đo được (Qwen3.7 Flash, 6 luồng, mỗi bộ chạy 1 lần)
| Bộ dữ liệu | Exact Match | Trung vị / chậm nhất mỗi câu | Token/câu | Chi phí/câu |
|---|---|---|---|---|
| Public 2 của BTC (30 câu), dùng để debug nên kết quả lạc quan | 30/30 | 9.9 s / 171 s | 17k | ≈ $0.0009 |
| Public 1 của BTC (20 câu) | 16/20 | 31 s / 143 s | 26k | ≈ $0.0014 |
| Bộ robustness tự xây (26 câu, chưa dùng để chỉnh) | 26/26 | 5.7 s / 62 s | 3k | ≈ $0.0002 |
| Bộ nhiều file tự xây (37 câu, chưa dùng để chỉnh) | 34/37 | 39 s / 583 s | 55k | ≈ $0.0028 |

Chi phí tính theo giá niêm yết của OpenRouter ($0.03/M token input, $0.13/M token output), giới hạn của đề là $0.10/câu. Public 2 chạy hết 3 phút 50 giây cho 30 câu, RAM đỉnh khoảng 190 MB. Câu hỏi trên nhiều file tốn token và thời gian nhiều hơn rõ rệt.

## Cấu trúc thư mục
```
run.py            điểm vào dòng lệnh
llm.py            gọi Qwen3.7 Flash qua OpenRouter (cấu hình từ biến môi trường / .env)
excel_parser.py   đọc và khôi phục cấu trúc bảng (chạy riêng: python excel_parser.py file.xlsx để xem kết quả parse)
formatting.py     định dạng đáp án
engine/ir.py      TableIR
engine/v3.py      DataFrame cho chương trình, bản đồ và lớp ngữ nghĩa cột, ứng viên, bỏ phiếu, trọng tài
evaluate.py       chấm Exact Match
docs/             sơ đồ kiến trúc (draw.io)
```

## Giới hạn đã biết
- Code do LLM sinh chạy bằng `exec()` trong tiến trình con. Cách này cô lập lỗi và có timeout, nhưng **chưa phải sandbox bảo mật** (tiến trình con vẫn đọc/ghi được file và truy cập được mạng).
- Parser dùng heuristic. Layout quá lạ (khác hẳn bảng thống kê hay bảng bản ghi) có thể bị đọc sai cấu trúc.
- Các câu cần ngầm hiểu quy ước riêng của người ra đề (không nêu trong câu hỏi) có thể bị trả lời theo cách hiểu khác.
