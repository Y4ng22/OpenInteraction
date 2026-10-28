## fdb-handoff (LLM judge), $RESULT references resolved

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 59/100 (59%) |
| pass rate, benchmark errors counted as passed | 67/100 (67%) |
| tool selection F1 (mean) | 0.856 |
| argument accuracy (mean) | 0.678 |
| items with a handoff | 0/100 (0%) |
| FDB calls made by frontend / backend | 133 / 0 |
| unrelated harness tool calls | 0 |
| errors | 0 |
| frontend first token, median (s) | 0.86 |
| end to end, median (s) | 6.14 |

| difficulty | pass rate |
| --- | --- |
| easy | 26/36 (72%) |
| medium | 20/34 (59%) |
| hard | 13/30 (43%) |
| self-correction items | 10/17 (59%) |

Failures:

- ecommerce_04_6998abd731d2ec50d067d5bd: Wrong arguments for: ['track_order']
- ecommerce_12_6998abd731d2ec50d067d5bd: Missing tools: ['search_products']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart', 'track_order']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart']
- ecommerce_19_66f59c766e7e22e1f90d08f6: Missing tools: ['add_to_cart']
- ecommerce_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['add_to_cart']
- ecommerce_25_65e8cf8f4c7424fa062e54a3: Missing tools: ['add_to_cart']
- ecommerce_25_69a9cf80f4d7668d5c815038: Wrong arguments for: ['add_to_cart']
- finance_08_65e8cf8f4c7424fa062e54a3: Missing tools: ['get_exchange_rate']
- finance_08_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_11_695bd157114f0d2317f88617: Missing tools: ['modify_autopay']
- finance_14_5f4a4da1575d605c43bef871: Missing tools: ['get_exchange_rate', 'get_exchange_rate']
- finance_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['modify_autopay']
- housing_03_5f4a4da1575d605c43bef871: Wrong arguments for: ['update_search_filter']
- housing_05_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_apartments']
- housing_05_61517db6a7589569521b2356: Wrong arguments for: ['search_apartments']
- housing_08_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['update_search_filter']
- housing_10_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['calculate_commute', 'update_search_filter']
- housing_13_5f4a4da1575d605c43bef871: Wrong arguments for: ['update_search_filter']
- housing_15_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_apartments']
- housing_15_61517db6a7589569521b2356: Wrong arguments for: ['search_apartments']
- housing_17_65e8cf8f4c7424fa062e54a3: Missing tools: ['calculate_commute']
- housing_17_69a9cf80f4d7668d5c815038: Missing tools: ['calculate_commute']
- housing_18_66c4f3cb14cbfc4db836bd4e: Missing tools: ['calculate_commute']
- housing_20_62a885d5b6af18b3d4579e1b: Missing tools: ['calculate_commute', 'update_search_filter']
- housing_21_66c4f3cb14cbfc4db836bd4e: Wrong arguments for: ['calculate_commute']
- housing_22_695bd157114f0d2317f88617: Wrong arguments for: ['update_search_filter']
- housing_24_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['calculate_commute']
- housing_24_69a9cf80f4d7668d5c815038: Missing tools: ['update_search_filter', 'calculate_commute']
- housing_25_66f59c766e7e22e1f90d08f6: Unexpected tools: ['update_search_filter', 'search_apartments']
- travel_02_5e3c1fbece3a7b000a6fd95a: Wrong arguments for: ['update_identity_doc']
- travel_07_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['update_identity_doc']
- travel_07_61517db6a7589569521b2356: Wrong arguments for: ['update_identity_doc']
- travel_10_5f4a4da1575d605c43bef871: Missing tools: ['search_flights']
- travel_11_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_flights']
- travel_16_66f59c766e7e22e1f90d08f6: Missing tools: ['search_flights']; Unexpected tools: ['book_flight']
- travel_19_695bd157114f0d2317f88617: Missing tools: ['search_flights']
- travel_20_62a885d5b6af18b3d4579e1b: Missing tools: ['search_flights', 'book_flight', 'update_identity_doc']; Unexpected tools: ['update_search_filter', 'update_search_filter', 'update_search_filter', 'update_search_filter']
- travel_23_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['update_identity_doc']
- travel_23_69a9cf80f4d7668d5c815038: Wrong arguments for: ['update_identity_doc']
- travel_24_695bd157114f0d2317f88617: Missing tools: ['book_flight']; Unexpected tools: ['search_apartments']
