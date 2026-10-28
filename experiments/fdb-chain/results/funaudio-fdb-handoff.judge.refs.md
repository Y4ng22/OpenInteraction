## funaudio-fdb-handoff (LLM judge), $RESULT references resolved

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 51/100 (51%) |
| pass rate, benchmark errors counted as passed | 61/100 (61%) |
| tool selection F1 (mean) | 0.859 |
| argument accuracy (mean) | 0.783 |
| items with a handoff | 1/100 (1%) |
| FDB calls made by frontend / backend | 257 / 0 |
| unrelated harness tool calls | 0 |
| errors | 0 |
| end to end, median (s) | 6.59 |

| difficulty | pass rate |
| --- | --- |
| easy | 24/36 (67%) |
| medium | 19/34 (56%) |
| hard | 8/30 (27%) |
| self-correction items | 10/17 (59%) |

Failures:

- ecommerce_02_5f4a4da1575d605c43bef871: Unexpected tools: ['add_to_cart']
- ecommerce_05_695bd157114f0d2317f88617: Unexpected tools: ['add_to_cart']
- ecommerce_08_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['add_to_cart']
- ecommerce_08_61517db6a7589569521b2356: Unexpected tools: ['add_to_cart']
- ecommerce_09_695bd157114f0d2317f88617: Unexpected tools: ['add_to_cart']
- ecommerce_10_6998abd731d2ec50d067d5bd: Unexpected tools: ['add_to_cart']
- ecommerce_12_6998abd731d2ec50d067d5bd: Unexpected tools: ['add_to_cart']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart', 'track_order']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart']
- ecommerce_16_695bd157114f0d2317f88617: Unexpected tools: ['add_to_cart']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Unexpected tools: ['search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order']
- ecommerce_19_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['add_to_cart']
- ecommerce_21_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['add_to_cart']
- ecommerce_21_69a9cf80f4d7668d5c815038: Wrong arguments for: ['add_to_cart']
- ecommerce_25_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['add_to_cart']
- ecommerce_25_69a9cf80f4d7668d5c815038: Wrong arguments for: ['add_to_cart']
- finance_18_6998abd731d2ec50d067d5bd: Unexpected tools: ['update_identity_doc', 'search_flights', 'search_products', 'book_flight', 'add_to_cart', 'search_apartments', 'calculate_commute', 'update_search_filter']
- finance_20_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['modify_autopay']
- housing_05_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['update_search_filter', 'search_apartments', 'update_search_filter']
- housing_05_61517db6a7589569521b2356: Unexpected tools: ['update_search_filter', 'search_apartments', 'update_search_filter', 'search_apartments']
- housing_08_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['update_search_filter']
- housing_10_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['calculate_commute']
- housing_11_62a885d5b6af18b3d4579e1b: Unexpected tools: ['update_search_filter', 'update_search_filter', 'search_apartments', 'add_to_cart']
- housing_14_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['update_search_filter', 'search_apartments', 'update_search_filter', 'search_apartments', 'update_search_filter']
- housing_14_61517db6a7589569521b2356: Unexpected tools: ['update_search_filter', 'search_apartments']
- housing_15_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['update_search_filter', 'search_apartments']
- housing_15_61517db6a7589569521b2356: Unexpected tools: ['update_search_filter', 'search_apartments']
- housing_18_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['update_search_filter', 'update_search_filter', 'search_apartments', 'update_search_filter']
- housing_20_62a885d5b6af18b3d4579e1b: Unexpected tools: ['search_apartments', 'calculate_commute']
- housing_21_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['update_search_filter', 'update_search_filter', 'search_apartments', 'calculate_commute']
- housing_22_695bd157114f0d2317f88617: Unexpected tools: ['book_flight', 'get_card_benefits', 'get_exchange_rate', 'update_identity_doc', 'modify_autopay', 'track_order', 'search_products']
- housing_24_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['update_search_filter', 'search_apartments', 'calculate_commute', 'update_search_filter', 'search_apartments', 'calculate_commute', 'update_search_filter', 'search_apartments', 'calculate_commute']
- housing_24_69a9cf80f4d7668d5c815038: Unexpected tools: ['update_identity_doc', 'get_card_benefits', 'get_exchange_rate', 'modify_autopay', 'search_flights', 'track_order', 'search_products', 'add_to_cart', 'book_flight', 'update_search_filter', 'search_products', 'add_to_cart', 'calculate_commute', 'search_flights', 'track_order', 'get_exchange_rate']
- housing_25_66f59c766e7e22e1f90d08f6: Unexpected tools: ['update_search_filter', 'search_apartments', 'update_search_filter', 'search_apartments', 'update_identity_doc']
- travel_01_62a885d5b6af18b3d4579e1b: Unexpected tools: ['book_flight']
- travel_02_5e3c1fbece3a7b000a6fd95a: Wrong arguments for: ['update_identity_doc']
- travel_05_6998abd731d2ec50d067d5bd: Unexpected tools: ['book_flight']
- travel_07_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['update_identity_doc']
- travel_07_61517db6a7589569521b2356: Wrong arguments for: ['update_identity_doc']
- travel_10_5f4a4da1575d605c43bef871: Unexpected tools: ['book_flight']
- travel_11_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_flights']
- travel_16_66f59c766e7e22e1f90d08f6: Unexpected tools: ['book_flight']
- travel_19_695bd157114f0d2317f88617: Wrong arguments for: ['search_flights']
- travel_20_62a885d5b6af18b3d4579e1b: Unexpected tools: ['update_identity_doc']
- travel_21_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['get_exchange_rate']
- travel_21_69a9cf80f4d7668d5c815038: Unexpected tools: ['get_exchange_rate']
- travel_23_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['update_identity_doc']
- travel_23_69a9cf80f4d7668d5c815038: Wrong arguments for: ['update_identity_doc']
- travel_24_695bd157114f0d2317f88617: Unexpected tools: ['search_apartments', 'book_flight', 'add_to_cart']
