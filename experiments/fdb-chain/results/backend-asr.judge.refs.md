## backend-asr (LLM judge), $RESULT references resolved

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 70/100 (70%) |
| pass rate, benchmark errors counted as passed | 80/100 (80%) |
| tool selection F1 (mean) | 0.982 |
| argument accuracy (mean) | 0.780 |
| items with a handoff | 100/100 (100%) |
| FDB calls made by frontend / backend | 0 / 143 |
| unrelated harness tool calls | 0 |
| errors | 0 |
| end to end, median (s) | 2.02 |

| difficulty | pass rate |
| --- | --- |
| easy | 29/36 (81%) |
| medium | 26/34 (76%) |
| hard | 15/30 (50%) |
| self-correction items | 14/17 (82%) |

Failures:

- ecommerce_06_66c4f3cb14cbfc4db836bd4e: Wrong arguments for: ['add_to_cart']
- ecommerce_11_62a885d5b6af18b3d4579e1b: Wrong arguments for: ['add_to_cart']
- ecommerce_12_6998abd731d2ec50d067d5bd: Wrong arguments for: ['search_products']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Missing tools: ['add_to_cart']
- ecommerce_20_66c4f3cb14cbfc4db836bd4e: Wrong arguments for: ['add_to_cart']
- ecommerce_21_65e8cf8f4c7424fa062e54a3: Missing tools: ['add_to_cart']
- ecommerce_21_69a9cf80f4d7668d5c815038: Wrong arguments for: ['track_order']
- ecommerce_23_5f4a4da1575d605c43bef871: Missing tools: ['add_to_cart']
- finance_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['modify_autopay']
- housing_05_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_apartments']
- housing_05_61517db6a7589569521b2356: Wrong arguments for: ['search_apartments']
- housing_08_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['update_search_filter']
- housing_14_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_apartments']
- housing_14_61517db6a7589569521b2356: Wrong arguments for: ['search_apartments']
- housing_15_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_apartments']
- housing_15_61517db6a7589569521b2356: Wrong arguments for: ['search_apartments']
- housing_18_66c4f3cb14cbfc4db836bd4e: Wrong arguments for: ['search_apartments']
- housing_20_62a885d5b6af18b3d4579e1b: Missing tools: ['update_search_filter']
- housing_21_66c4f3cb14cbfc4db836bd4e: Wrong arguments for: ['search_apartments', 'calculate_commute']
- housing_22_695bd157114f0d2317f88617: Wrong arguments for: ['update_search_filter', 'search_apartments', 'calculate_commute']
- housing_25_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['update_search_filter']
- travel_02_5e3c1fbece3a7b000a6fd95a: Wrong arguments for: ['update_identity_doc']
- travel_07_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['update_identity_doc']
- travel_07_61517db6a7589569521b2356: Wrong arguments for: ['update_identity_doc']
- travel_11_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_flights']
- travel_20_62a885d5b6af18b3d4579e1b: Missing tools: ['book_flight']
- travel_23_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['update_identity_doc']
- travel_23_69a9cf80f4d7668d5c815038: Wrong arguments for: ['update_identity_doc']
- travel_24_695bd157114f0d2317f88617: Wrong arguments for: ['book_flight']
