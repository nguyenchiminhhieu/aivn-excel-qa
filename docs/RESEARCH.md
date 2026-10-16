# Nghiên cứu các giải pháp hàng đầu cho hỏi đáp trên bảng phân cấp và áp dụng vào hệ thống

Cập nhật 2026-10-16. Các mục đánh dấu *(chưa kiểm chứng)* là phần không xác nhận được từ paper hoặc code.

## 1. Bối cảnh: dữ liệu của BTC lấy từ đâu
- 17/25 file xlsx public (cả bộ 1 lẫn bộ 2) là **bản sao nguyên vẹn** của bảng trong **RealHiTBench**: trùng MD5 và kích thước file. RealHiTBench thuộc Findings of ACL 2025, arXiv 2506.13405, dữ liệu ở `huggingface.co/datasets/spzy/RealHiTBench`, giấy phép CC-BY-NC-4.0.
- 8 file còn lại không thuộc RealHiTBench:
  - một bảng tự tạo về người;
  - các báo cáo tài chính nhiều sheet;
  - các bảng Statistics Canada, có bảng đã dịch sang tiếng Việt hoặc tiếng Trung.
- Có 21/50 câu hỏi là câu của RealHiTBench được thêm dòng "Answer with only…", và đáp án giống hệt. 16 câu do BTC tự viết. Các quy ước lạ của đáp án nằm ở phần BTC tự viết, ví dụ "trung bình tính cả cột Total" hay "tuổi tại ngày nhập học".
- RealHiTBench có 5 nhóm câu hỏi: Fact Checking, Numerical Reasoning, Data Analysis, Visualization, Structure Comprehending. Đề thi chỉ dùng các câu trả lời ngắn chấm EM, tương ứng Fact Checking và Numerical Reasoning.
- **Không có leaderboard chính thức.** Mỗi paper tự báo số trên một tập con hoặc một thước đo khác nhau, nên các con số dưới đây chỉ so sánh được một cách tương đối.

## 2. Các giải pháp đã đọc (paper và code)

| Giải pháp | Nguồn, code | Cơ chế chính | Bằng chứng | Hạn chế |
|---|---|---|---|---|
| **TreeThinker** | Phương pháp của chính paper RealHiTBench; `cspzyy/RealHiTBench` (script chính chỉ còn dạng bytecode) | Vòng 1: LLM mã hóa header thành tuple (R/C+level, start, end, text) rồi dựng cây. Vòng 2: trích từ khóa, khớp với nút header, ReAct (phần "action" chỉ là giả lập, không thực thi) | GPT-4o: NR 38.7 → 53.3 EM. Bỏ bước dựng cây: −8 | Cây do LLM dựng, tốn 2 lần gọi. Làm giảm điểm của model mạnh khi có tool (Qwen3-Coder-480B, theo SpreadsheetAgent). Mức tăng EM lớn hơn mức tăng F1, nên một phần có thể là hiệu ứng định dạng |
| **SpreadsheetAgent** | ACL 2026, arXiv 2604.12282; `renhouxing/SpreadsheetAgent` | Tác tử trích cấu trúc (code, vision, LaTeX theo vùng A1) → bản đồ YAML → tác tử kiểm chứng (tối đa 3 vòng) → solver | RealHiTBench (Qwen3-30B): FC 36.2 → 46.2. SpreadsheetBench: +4–11 | Tăng chủ yếu khi **model lớn trích cấu trúc cho model nhỏ dùng**; model tự trích cho mình chỉ tăng +1–2. Tốn 97 giây và 19.5 lần gọi cho mỗi sheet. Code có lỗi đọc tên file YAML |
| **DTR** | arXiv 2603.09151, chỉ là preprint, không có code | Meta-graph, kho phép toán, chọn đường đi bằng p-UCB, bộ nhớ kinh nghiệm, bỏ phiếu | DeepSeek-V3: FC 58.2 / NR 55.5. Ablation các thành phần: +0.4 đến +1.4 | Không giúp được model nhỏ (Qwen3-4B kém TableGPT2-7B). Phương trình và thuật toán mâu thuẫn nhau; baseline yếu |
| **SemiBonsai** | arXiv 2610.07749; `DataAutonomyLab/SemiBonsai` | Cây header theo luật cộng VLM; **bộ giải quyết mơ hồ: cụm từ → nút header bằng n-gram**, cắt tỉa theo ngữ cảnh cục bộ, viết lại câu hỏi; chọn kế hoạch | Tập số học của RealHiTBench: 0.744 (GPT-4.1). Cây do LLM dựng 0.41, **cây chuẩn 0.89**. Bỏ bộ giải quyết mơ hồ: −15 (HiTab) | Cần VLM để đề xuất header |
| **TabGR** | arXiv 2601.08444; `yxw-11/TabGR` | Mỗi ô thành triple ⟨hàng, header, giá trị⟩, xếp hạng bằng PageRank có định hướng theo câu hỏi, rồi CoT có dẫn chứng | RealHiTBench (Llama3.3-70B): 54.1 tổng. Triple 80.1 so với Markdown 75.5 (WikiTQ) | Thước đo "Accuracy" không định nghĩa rõ |
| **SheetBrain** | arXiv 2510.19247; `microsoft/SheetBrain` | Hiểu (tóm tắt sheet + vị trí ô) → sandbox Python → kiểm chứng và chạy lại | 300 câu RealHiTBench chấm bằng LLM judge: 78.3 (so với 70.0). **Thêm vị trí ô: 63 → 75** | Không có số EM |
| **ST-Raptor** | arXiv 2508.18190 | Cây trực giao (header và thân bảng), pipeline phép toán trên cây, kiểm xuôi và kiểm ngược | SSTQA: 72.4. Bỏ cây −15, bỏ bộ kiểm chứng −6 | Trên tập số học RealHiTBench chỉ đạt 0.31 (theo SemiBonsai chạy lại) |
| **E5** | NAACL 2024 | Tự giải thích cấu trúc header, rồi trích ô bằng pandas | HiTab (GPT-4): 85.1 so với 83.2 khi bỏ bước giải thích | Mới thử với GPT-4 |
| **TableGPT-R1** | arXiv 2512.20312 | SFT + RL trên Qwen3-8B | FC 63.9 / NR 49.0 | Cần huấn luyện; không áp dụng được |

Mốc tham chiếu (RealHiTBench, đầu vào text): DeepSeek-R1 đạt FC 70.9, NR 70.3, SC 82.7; GPT-4o đạt FC 60.3, NR 38.7.

## 3. Các kỹ thuật, xếp theo độ mạnh của bằng chứng

1. **Cấu trúc header và cây hàng đúng.** Đây là đòn bẩy lớn nhất: khoảng cách giữa cây chuẩn và cây do LLM dựng là +0.45 (SemiBonsai). Cây do code tất định dựng tốt hơn cây do LLM dựng.
2. **Chạy code cho câu số học** (E5, SheetBrain, ST-Raptor).
3. **Neo cụm từ trong câu hỏi vào nút header** (SemiBonsai, TreeThinker).
4. **Serialize kèm đầy đủ đường dẫn header và vị trí ô** (TabGR, SheetBrain).
5. **Kiểm chứng đáp án, kiểm ngược** (ST-Raptor, SheetBrain, SpreadsheetAgent).
6. **Tự giải thích cấu trúc hoặc lập kế hoạch trước khi viết code** (E5, DTR [THINK]+[CODE]).
7. **Chọn ngữ cảnh theo mức liên quan** (TabGR, SemiBonsai).
8. **Lọc ứng viên theo kiểu đáp án trước khi bỏ phiếu** (DTR f_type).

Không áp dụng: bandit p-UCB và bộ nhớ kinh nghiệm (mức tăng +0.4–0.9, không có code); vision; agent dùng model 480B; huấn luyện RL.

## 4. Đối chiếu với hệ thống và việc đã áp dụng

| Kỹ thuật | Trước | Đã áp dụng (nhánh `dev`) |
|---|---|---|
| 1. Cây header đúng | Parser tất định: thụt lề, section, khoảng lồng nhau, tổng nhất quán, khối lặp, header xuống dòng, mã định danh, nhãn thời gian | Giữ nguyên; tiếp tục đầu tư vào parser |
| 2. Chạy code | Chương trình pandas trong tiến trình con | Giữ nguyên |
| 3. Neo cụm từ vào header | Chưa có | `engine/ground.py`: tách n-gram của câu hỏi, khớp với đường dẫn hàng/cột, trọng số IDF, loại cụm bị bao bởi cụm dài hơn. Không tốn lần gọi LLM |
| 4. Đường dẫn và vị trí ô | Bản đồ có đường dẫn hàng và giá trị theo thứ tự cột | Khối **FOCUS**: mỗi ô liên quan ghi `row N [đường dẫn] 'cột'=giá trị (A1)`, đặt trước MAP |
| 7. Ngữ cảnh theo mức liên quan | Bảng lớn chỉ có profile + mẫu cố định | FOCUS chọn tối đa 20 hàng và 6 cột liên quan nhất, kể cả trong bảng lớn |
| 6. Kế hoạch trước code | Một câu diễn giải | Kế hoạch JSON có thêm trường `cells` (hàng/cột sẽ dùng), bắt buộc trước khi viết code |
| 8. Lọc theo kiểu đáp án | Chỉ kiểm tra có căn cứ trong dữ liệu | Suy ra kiểu cần trả lời (Yes/No, số, danh sách, có hỗ trợ VI/KO/ZH) và loại ứng viên sai kiểu trước khi bỏ phiếu (chỉ khi còn ít nhất một ứng viên đúng kiểu) |
| 5. Kiểm ngược | Có cổng đồng thuận giữa hai cách suy luận | Chưa làm: tốn thêm một lần gọi, mức tăng nhỏ với model nhỏ |

## 5. Ghi chú về định dạng EM
- Bộ chấm của RealHiTBench chuẩn hóa nhiều thứ: lowercase, bỏ dấu câu, làm tròn 1 chữ số thập phân. Đề thi thì so khớp chính xác (theo `answers.json`), nên **không thể trông vào phần chuẩn hóa đó**.
- Quy tắc định dạng nên giữ:
  - Yes/No cho câu hỏi đúng/sai.
  - Tên thực thể ngắn nhất, đúng chính tả trong bảng.
  - Theo đúng số chữ số thập phân câu hỏi yêu cầu.
  - Giữ thứ tự danh sách.
