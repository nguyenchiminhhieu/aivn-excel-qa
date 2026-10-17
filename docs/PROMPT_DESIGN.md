# Thiết kế prompt: quy ước đáp án, ví dụ đã giải, quy trình đánh giá

Cập nhật 2026-10-17. Ghi lại *vì sao* prompt được viết như hiện tại, để mọi thay đổi sau đều kiểm chứng được.

## 1. Vấn đề
Đáp án của BTC theo những quy ước không phải lúc nào câu hỏi cũng nói ra. Ví dụ:
- "trung bình trên tất cả các cột" tính cả cột Total (FAQ câu 01);
- tuổi được đo tại ngày nhập học.

Tập ẩn có thể chứa những quy ước khác mà ta chưa thấy. Vì vậy prompt phải nêu **nguyên tắc chung**, không vá theo từng câu public.

## 2. Bằng chứng đã dùng (đã đối chiếu với bài báo gốc)

| Quyết định | Bằng chứng |
|---|---|
| Ghi quy ước thành luật tường minh, do người viết | BIRD (NeurIPS 2023): thêm "evidence" tăng 13–20 điểm; luật do người viết đạt 46.67, luật do model sinh 37.94 |
| Ít luật, mỗi luật một ý, luật quan trọng đặt trước | IFScale (2025), FollowBench (ACL 2024): model nhỏ tuân thủ kém dần khi số chỉ dẫn tăng |
| Một khối luật dùng chung, giống hệt nhau ở mọi prompt, có thứ tự ưu tiên | IHEval (NAACL 2025): model mở tốt nhất chỉ giải đúng 48% các xung đột chỉ dẫn |
| Model nêu cách hiểu trước khi tính (`assumptions`, dòng `Reading:`) | AMBROSIA (NeurIPS 2024): khi không được nhắc, model chọn theo thói quen (83–98%). Nêu cách hiểu trước nâng độ bao phủ cách hiểu đúng từ 29.4 lên 81.9 |
| Ví dụ **giống câu hỏi** (không ngẫu nhiên), che từ thuộc miền khi so độ giống | DAIL-SQL (VLDB 2024): EM 22.1 → 71.9 với ví dụ giống, 51.6 với ví dụ ngẫu nhiên |
| Luật định dạng nằm trong code, không trong prompt | Nhiều nghiên cứu cho thấy định dạng prompt làm điểm dao động mạnh; `formatting.py` làm việc này tất định |
| Không dùng critic hay trọng tài thiếu bằng chứng mới | Mix-SC (NAACL 2024): để model tự chọn 64.99 so với bỏ phiếu 73.06; Huang et al. (ICLR 2024); thí nghiệm của team: critic −2.7 điểm |

## 3. Chín quy ước và nguồn gốc
Mỗi quy ước cần có FAQ xác nhận hoặc bằng chứng từ ít nhất 2 bảng trong đáp án công khai. Quy ước chỉ có 1 bảng phải được kiểm tra riêng (ablation) trước khi giữ.

| # | Quy ước (rút gọn) | Nguồn | Đạt chuẩn ≥ 2 bảng? |
|---|---|---|---|
| 1 | Câu hỏi nêu cấp hoặc bộ lọc thì dùng đúng cấp đó | P2 câu 5 ("directly under"), 14 ("excluding … Total"), 25 ("không tính dòng any") | Có |
| 2 | "All / across all" khi không nêu cấp: tính mọi mục, kể cả mục tổng hợp; trung bình không trọng số | **FAQ câu 01** | Có (FAQ) |
| 3 | Không nêu cấp, không nói "all": so các mục cấp trên cùng, không gộp cha với con, không tính tổng toàn bảng | p4_q016 (p4_t08), P2 câu 1 (p2_t01) | Có |
| 4 | Một dòng và một cột thì lấy ô giao nhau; chỉ cộng khi câu hỏi nói cộng | p4_q015 (p4_t08), p4_q006 (p4_t04) | Có |
| 5 | Tuổi hoặc thời gian trôi qua đo tại ngày sự kiện của bản ghi, xếp theo số ngày | p4_q001–003 (cùng p4_t01) | **Chưa: 1 bảng, cần ablation** |
| 6 | Định nghĩa thay đổi, % thay đổi, điểm phần trăm, tỷ số | Định nghĩa toán học chuẩn; P2 câu 3, 11, 13, 24, 26, 27 | Có |
| 7 | Tỷ lệ, trung bình, chỉ số không cộng thành tổng, trừ khi câu hỏi yêu cầu | P2 câu 25 (cộng hai tỷ lệ theo yêu cầu), cột not-additive | Có |
| 8 | Giữ nguyên đơn vị của bảng | P2 câu 6 ("by how many thousand"), p4_q008 (lỗi 9017000) | Có |
| 9 | Tên lấy theo nhãn bảng; câu hỏi liệt kê lựa chọn thì dùng cách viết của câu hỏi | p4_q013 (p4_t07), P2 câu 10 (p2_t04) | Có |

## 4. Cấu hình (biến môi trường)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `QA_CONV_POS` | `system` | `system`: quy ước nằm trong system prompt. `end`: nằm sau dữ liệu, ngay trước câu hỏi |
| `QA_EXAMPLES` | `0` | Số ví dụ đã giải chèn vào mỗi câu (0 = tắt). Ví dụ lấy từ `excelqa/examples_bank.json`, không bao giờ cùng workbook với câu hỏi, chỉ chèn khi độ giống ≥ 0.40 |

Chất lượng truy xuất ví dụ, đo trên tập kiểm chứng của `testset_manual`: 59% ví dụ được chọn có chung tag suy luận với câu hỏi, so với 19% khi chọn ngẫu nhiên. 27/39 câu có ví dụ.

## 5. Quy trình đánh giá (chống overfit)
- **Tập test niêm phong:** 50 câu public của BTC. Chỉ chạy để xác nhận lần cuối, không dùng để chỉnh.
- **Chia theo workbook** (`bench_btc/make_splits.py`): phân tích 160 câu (phân tích lỗi và kho ví dụ), kiểm chứng 194 câu (chỉ dùng để chọn phương án), loại 17 câu nhiều file nằm ở cả hai tập.
- **Đo nhiễu trước:** chạy baseline nhiều lần. Chênh lệch tối thiểu đáng tin trên khoảng 200 câu là khoảng 3 điểm.
- **Chấp nhận một thay đổi khi:**
  - tăng từ 3 điểm trở lên trên tập kiểm chứng, có ý nghĩa thống kê qua 2 lần chạy;
  - không giảm trên tập test niêm phong;
  - mỗi quy ước có nguồn theo mục 3.
- **Chấm theo cách chuẩn hóa của FAQ câu 04** (`bench_btc/suite.py`): chữ thường; bỏ khoảng trắng thừa; bỏ gạch ngang, ngoặc, nháy; dấu phẩy thập phân thành dấu chấm; không làm tròn số.
