## funaudio-fdb-delegate-tid (exact match), $RESULT references resolved

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 51/100 (51%) |
| pass rate, benchmark errors counted as passed | 61/100 (61%) |
| tool selection F1 (mean) | 0.962 |
| argument accuracy (mean) | 0.592 |
| items with a handoff | 100/100 (100%) |
| FDB calls made by frontend / backend | 0 / 156 |
| unrelated harness tool calls | 0 |
| errors | 0 |
| end to end, median (s) | 5.70 |

| difficulty | pass rate |
| --- | --- |
| easy | 23/36 (64%) |
| medium | 19/34 (56%) |
| hard | 9/30 (30%) |
| self-correction items | 10/17 (59%) |

Failures:

- ecommerce_12_6998abd731d2ec50d067d5bd: Wrong arguments for: ['search_products']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart', 'track_order']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Wrong arguments for: ['search_products', 'track_order']
- ecommerce_20_66c4f3cb14cbfc4db836bd4e: Wrong arguments for: ['search_products']
- ecommerce_25_69a9cf80f4d7668d5c815038: Wrong arguments for: ['track_order']
- finance_18_6998abd731d2ec50d067d5bd: Unexpected tools: ['get_exchange_rate', 'track_order', 'get_card_benefits', 'track_order', 'get_exchange_rate', 'get_card_benefits', 'track_order', 'get_exchange_rate', 'get_card_benefits']
- finance_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['modify_autopay']; Unexpected tools: ['track_order']
- housing_03_5f4a4da1575d605c43bef871: Wrong arguments for: ['update_search_filter']
- housing_05_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_apartments']
- housing_05_61517db6a7589569521b2356: Wrong arguments for: ['search_apartments']
- housing_06_5f4a4da1575d605c43bef871: Wrong arguments for: ['calculate_commute']
- housing_08_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['update_search_filter']
- housing_10_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['calculate_commute', 'update_search_filter']
- housing_13_5f4a4da1575d605c43bef871: Wrong arguments for: ['update_search_filter', 'update_search_filter']
- housing_14_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['update_search_filter']
- housing_14_61517db6a7589569521b2356: Wrong arguments for: ['search_apartments']
- housing_15_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_apartments']
- housing_15_61517db6a7589569521b2356: Wrong arguments for: ['search_apartments']
- housing_17_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['calculate_commute']
- housing_17_69a9cf80f4d7668d5c815038: Wrong arguments for: ['calculate_commute']
- housing_18_66c4f3cb14cbfc4db836bd4e: Missing tools: ['calculate_commute']
- housing_19_62a885d5b6af18b3d4579e1b: Wrong arguments for: ['calculate_commute']
- housing_20_62a885d5b6af18b3d4579e1b: Missing tools: ['update_search_filter']; Unexpected tools: ['search_apartments']
- housing_21_66c4f3cb14cbfc4db836bd4e: Missing tools: ['calculate_commute']
- housing_22_695bd157114f0d2317f88617: Wrong arguments for: ['update_search_filter', 'calculate_commute']
- housing_24_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['update_search_filter', 'calculate_commute']
- housing_24_69a9cf80f4d7668d5c815038: Wrong arguments for: ['update_search_filter', 'calculate_commute']
- housing_25_66f59c766e7e22e1f90d08f6: Missing tools: ['search_apartments']; Unexpected tools: ['update_search_filter']
- travel_01_62a885d5b6af18b3d4579e1b: Wrong arguments for: ['search_flights']
- travel_02_5e3c1fbece3a7b000a6fd95a: Wrong arguments for: ['update_identity_doc']
- travel_03_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_flights']
- travel_03_61517db6a7589569521b2356: Wrong arguments for: ['search_flights']
- travel_05_6998abd731d2ec50d067d5bd: Wrong arguments for: ['search_flights']
- travel_07_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['update_identity_doc']
- travel_07_61517db6a7589569521b2356: Wrong arguments for: ['update_identity_doc']
- travel_08_695bd157114f0d2317f88617: Wrong arguments for: ['search_flights']
- travel_10_5f4a4da1575d605c43bef871: Wrong arguments for: ['search_flights']
- travel_11_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_flights']
- travel_14_66c4f3cb14cbfc4db836bd4e: Wrong arguments for: ['search_flights']
- travel_16_66f59c766e7e22e1f90d08f6: Unexpected tools: ['book_flight']
- travel_18_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['search_flights']
- travel_19_695bd157114f0d2317f88617: Wrong arguments for: ['search_flights']
- travel_20_62a885d5b6af18b3d4579e1b: Missing tools: ['book_flight']
- travel_21_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['search_flights']
- travel_21_69a9cf80f4d7668d5c815038: Wrong arguments for: ['search_flights']
- travel_23_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['update_identity_doc']
- travel_23_69a9cf80f4d7668d5c815038: Wrong arguments for: ['update_identity_doc']
- travel_24_695bd157114f0d2317f88617: Missing tools: ['search_flights']
