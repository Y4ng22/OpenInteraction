## funaudio-fdb-native (LLM judge), $RESULT references resolved

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 48/100 (48%) |
| pass rate, benchmark errors counted as passed | 58/100 (58%) |
| tool selection F1 (mean) | 0.843 |
| argument accuracy (mean) | 0.793 |
| items with a handoff | 0/100 (0%) |
| FDB calls made by frontend / backend | 275 / 0 |
| unrelated harness tool calls | 0 |
| errors | 0 |
| end to end, median (s) | 6.31 |

| difficulty | pass rate |
| --- | --- |
| easy | 24/36 (67%) |
| medium | 18/34 (53%) |
| hard | 6/30 (20%) |
| self-correction items | 10/17 (59%) |

Failures:

- ecommerce_02_5f4a4da1575d605c43bef871: Unexpected tools: ['add_to_cart']
- ecommerce_05_695bd157114f0d2317f88617: Unexpected tools: ['add_to_cart']
- ecommerce_08_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['add_to_cart']
- ecommerce_08_61517db6a7589569521b2356: Unexpected tools: ['add_to_cart']
- ecommerce_09_695bd157114f0d2317f88617: Unexpected tools: ['add_to_cart']
- ecommerce_10_6998abd731d2ec50d067d5bd: Unexpected tools: ['add_to_cart', 'update_search_filter', 'update_search_filter']
- ecommerce_12_6998abd731d2ec50d067d5bd: Unexpected tools: ['add_to_cart']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart']
- ecommerce_16_695bd157114f0d2317f88617: Unexpected tools: ['add_to_cart']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Unexpected tools: ['add_to_cart']
- ecommerce_19_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['add_to_cart']
- ecommerce_21_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['add_to_cart']
- ecommerce_21_69a9cf80f4d7668d5c815038: Wrong arguments for: ['add_to_cart']
- ecommerce_23_5f4a4da1575d605c43bef871: Unexpected tools: ['modify_autopay']
- ecommerce_25_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['add_to_cart']
- ecommerce_25_69a9cf80f4d7668d5c815038: Wrong arguments for: ['add_to_cart']
- finance_18_6998abd731d2ec50d067d5bd: Unexpected tools: ['update_identity_doc', 'search_flights', 'search_products', 'book_flight', 'add_to_cart', 'search_apartments', 'calculate_commute', 'update_search_filter']
- finance_20_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['modify_autopay']
- housing_05_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['update_search_filter', 'search_apartments']
- housing_05_61517db6a7589569521b2356: Unexpected tools: ['update_search_filter', 'search_apartments', 'update_search_filter', 'search_apartments']
- housing_08_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['update_search_filter']
- housing_10_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['calculate_commute']
- housing_11_62a885d5b6af18b3d4579e1b: Unexpected tools: ['update_search_filter', 'update_search_filter']
- housing_14_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['update_search_filter']
- housing_14_61517db6a7589569521b2356: Unexpected tools: ['update_search_filter', 'search_apartments', 'update_search_filter']
- housing_15_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['update_search_filter', 'track_order', 'get_exchange_rate', 'get_card_benefits']
- housing_15_61517db6a7589569521b2356: Unexpected tools: ['update_search_filter', 'search_apartments', 'update_search_filter']
- housing_18_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['update_search_filter', 'update_search_filter', 'search_apartments', 'update_search_filter']
- housing_20_62a885d5b6af18b3d4579e1b: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_21_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['update_search_filter', 'update_search_filter', 'update_search_filter', 'search_apartments', 'calculate_commute', 'update_search_filter', 'update_search_filter', 'update_search_filter']
- housing_22_695bd157114f0d2317f88617: Unexpected tools: ['update_search_filter', 'search_apartments', 'calculate_commute', 'update_search_filter', 'search_apartments', 'calculate_commute']
- housing_24_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['update_identity_doc', 'get_card_benefits', 'get_exchange_rate', 'modify_autopay', 'search_products', 'add_to_cart', 'book_flight', 'track_order', 'search_flights', 'search_products', 'add_to_cart', 'update_search_filter', 'search_apartments', 'calculate_commute', 'get_exchange_rate', 'modify_autopay', 'search_products', 'add_to_cart']
- housing_24_69a9cf80f4d7668d5c815038: Unexpected tools: ['update_identity_doc', 'get_card_benefits', 'get_exchange_rate', 'modify_autopay', 'search_flights', 'track_order', 'search_products', 'add_to_cart', 'search_apartments', 'calculate_commute', 'update_identity_doc', 'get_card_benefits', 'get_exchange_rate', 'modify_autopay', 'search_flights', 'track_order', 'search_products', 'add_to_cart']
- housing_25_66f59c766e7e22e1f90d08f6: Unexpected tools: ['update_search_filter', 'search_apartments', 'update_search_filter', 'search_apartments', 'update_search_filter', 'search_apartments']
- travel_01_62a885d5b6af18b3d4579e1b: Unexpected tools: ['book_flight']
- travel_02_5e3c1fbece3a7b000a6fd95a: Wrong arguments for: ['update_identity_doc']
- travel_05_6998abd731d2ec50d067d5bd: Unexpected tools: ['book_flight']
- travel_07_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['update_identity_doc']
- travel_07_61517db6a7589569521b2356: Wrong arguments for: ['update_identity_doc']
- travel_10_5f4a4da1575d605c43bef871: Unexpected tools: ['book_flight']
- travel_11_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_flights']
- travel_14_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['book_flight']
- travel_16_66f59c766e7e22e1f90d08f6: Unexpected tools: ['book_flight']
- travel_18_66f59c766e7e22e1f90d08f6: Unexpected tools: ['search_flights', 'book_flight', 'update_identity_doc', 'search_flights', 'book_flight', 'update_identity_doc']
- travel_19_695bd157114f0d2317f88617: Unexpected tools: ['book_flight']
- travel_20_62a885d5b6af18b3d4579e1b: Wrong arguments for: ['update_identity_doc']
- travel_21_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['search_products', 'add_to_cart', 'search_apartments', 'update_search_filter']
- travel_21_69a9cf80f4d7668d5c815038: Unexpected tools: ['search_products', 'add_to_cart']
- travel_23_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['update_identity_doc']
- travel_23_69a9cf80f4d7668d5c815038: Wrong arguments for: ['update_identity_doc']
- travel_24_695bd157114f0d2317f88617: Unexpected tools: ['search_apartments', 'update_search_filter', 'add_to_cart', 'track_order', 'get_exchange_rate']
