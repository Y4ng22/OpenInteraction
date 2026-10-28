## fdb-delegate-manifest (exact match), $RESULT references resolved

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 4/100 (4%) |
| pass rate, benchmark errors counted as passed | 14/100 (14%) |
| tool selection F1 (mean) | 0.097 |
| argument accuracy (mean) | 0.043 |
| items with a handoff | 11/100 (11%) |
| FDB calls made by frontend / backend | 0 / 18 |
| unrelated harness tool calls | 110 |
| errors | 0 |
| frontend first token, median (s) | 0.68 |
| end to end, median (s) | 5.81 |

| difficulty | pass rate |
| --- | --- |
| easy | 1/36 (3%) |
| medium | 0/34 (0%) |
| hard | 3/30 (10%) |
| self-correction items | 1/17 (6%) |

Failures:

- ecommerce_01_65e8cf8f4c7424fa062e54a3: Missing tools: ['track_order']
- ecommerce_01_69a9cf80f4d7668d5c815038: Missing tools: ['track_order']
- ecommerce_02_5f4a4da1575d605c43bef871: Missing tools: ['search_products']
- ecommerce_04_6998abd731d2ec50d067d5bd: Missing tools: ['track_order']
- ecommerce_05_695bd157114f0d2317f88617: Missing tools: ['search_products']
- ecommerce_06_66c4f3cb14cbfc4db836bd4e: Missing tools: ['add_to_cart']
- ecommerce_07_66f59c766e7e22e1f90d08f6: Missing tools: ['track_order']
- ecommerce_08_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_products']
- ecommerce_08_61517db6a7589569521b2356: Missing tools: ['search_products']
- ecommerce_09_695bd157114f0d2317f88617: Missing tools: ['search_products']
- ecommerce_10_6998abd731d2ec50d067d5bd: Missing tools: ['track_order', 'search_products']
- ecommerce_11_62a885d5b6af18b3d4579e1b: Missing tools: ['add_to_cart']
- ecommerce_12_6998abd731d2ec50d067d5bd: Missing tools: ['search_products']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Missing tools: ['add_to_cart', 'track_order']
- ecommerce_13_61517db6a7589569521b2356: Missing tools: ['add_to_cart', 'track_order']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Missing tools: ['add_to_cart']
- ecommerce_14_61517db6a7589569521b2356: Missing tools: ['add_to_cart']
- ecommerce_15_56780a5a89319e0011644cb4: Missing tools: ['track_order']
- ecommerce_15_66c4f3cb14cbfc4db836bd4e: Missing tools: ['track_order']
- ecommerce_16_695bd157114f0d2317f88617: Missing tools: ['search_products']
- ecommerce_17_5f4a4da1575d605c43bef871: Missing tools: ['track_order']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Missing tools: ['search_products', 'add_to_cart', 'track_order']
- ecommerce_19_66f59c766e7e22e1f90d08f6: Missing tools: ['search_products', 'add_to_cart']
- ecommerce_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_products', 'add_to_cart']
- ecommerce_21_65e8cf8f4c7424fa062e54a3: Missing tools: ['track_order', 'search_products', 'add_to_cart']
- ecommerce_21_69a9cf80f4d7668d5c815038: Missing tools: ['add_to_cart']
- ecommerce_25_69a9cf80f4d7668d5c815038: Missing tools: ['track_order', 'search_products', 'add_to_cart']
- finance_01_65e8cf8f4c7424fa062e54a3: Missing tools: ['get_exchange_rate']
- finance_01_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_02_6998abd731d2ec50d067d5bd: Missing tools: ['get_card_benefits']
- finance_03_61517db6a7589569521b2356: Missing tools: ['modify_autopay']
- finance_04_62a885d5b6af18b3d4579e1b: Missing tools: ['get_exchange_rate']
- finance_06_66f59c766e7e22e1f90d08f6: Missing tools: ['get_card_benefits']
- finance_07_5ff07b5ee7a1d23e719e421e: Missing tools: ['modify_autopay']
- finance_07_61517db6a7589569521b2356: Missing tools: ['modify_autopay']
- finance_08_65e8cf8f4c7424fa062e54a3: Missing tools: ['get_exchange_rate']
- finance_08_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_10_66c4f3cb14cbfc4db836bd4e: Missing tools: ['get_card_benefits', 'get_exchange_rate']
- finance_11_695bd157114f0d2317f88617: Missing tools: ['modify_autopay']
- finance_12_65e8cf8f4c7424fa062e54a3: Missing tools: ['get_exchange_rate']
- finance_12_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_14_5f4a4da1575d605c43bef871: Missing tools: ['get_exchange_rate', 'get_exchange_rate']
- finance_15_66c4f3cb14cbfc4db836bd4e: Missing tools: ['modify_autopay']
- finance_16_62a885d5b6af18b3d4579e1b: Missing tools: ['get_exchange_rate']
- finance_17_6998abd731d2ec50d067d5bd: Missing tools: ['get_card_benefits']
- finance_18_6998abd731d2ec50d067d5bd: Missing tools: ['get_exchange_rate', 'modify_autopay', 'get_card_benefits']
- finance_19_66f59c766e7e22e1f90d08f6: Missing tools: ['get_exchange_rate']
- finance_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['get_exchange_rate', 'modify_autopay']
- finance_21_5e3c1fbece3a7b000a6fd95a: Missing tools: ['get_card_benefits', 'get_exchange_rate']
- finance_22_5f4a4da1575d605c43bef871: Missing tools: ['modify_autopay', 'modify_autopay']
- housing_01_62a885d5b6af18b3d4579e1b: Missing tools: ['search_apartments']
- housing_02_5f4a4da1575d605c43bef871: Missing tools: ['calculate_commute']
- housing_03_5f4a4da1575d605c43bef871: Missing tools: ['update_search_filter']
- housing_04_695bd157114f0d2317f88617: Missing tools: ['search_apartments']
- housing_05_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_apartments']
- housing_05_61517db6a7589569521b2356: Missing tools: ['search_apartments']
- housing_06_5f4a4da1575d605c43bef871: Missing tools: ['calculate_commute']
- housing_08_66f59c766e7e22e1f90d08f6: Missing tools: ['update_search_filter']
- housing_09_695bd157114f0d2317f88617: Missing tools: ['search_apartments']
- housing_10_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['calculate_commute', 'update_search_filter']
- housing_11_62a885d5b6af18b3d4579e1b: Missing tools: ['search_apartments']
- housing_13_5f4a4da1575d605c43bef871: Missing tools: ['update_search_filter', 'update_search_filter']
- housing_14_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_apartments']
- housing_14_61517db6a7589569521b2356: Missing tools: ['search_apartments']
- housing_15_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_apartments']
- housing_15_61517db6a7589569521b2356: Missing tools: ['search_apartments']
- housing_17_65e8cf8f4c7424fa062e54a3: Missing tools: ['calculate_commute']
- housing_17_69a9cf80f4d7668d5c815038: Missing tools: ['calculate_commute']
- housing_18_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_apartments', 'calculate_commute']
- housing_19_62a885d5b6af18b3d4579e1b: Wrong arguments for: ['calculate_commute']
- housing_20_62a885d5b6af18b3d4579e1b: Missing tools: ['search_apartments', 'calculate_commute', 'update_search_filter']
- housing_21_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_apartments', 'calculate_commute']
- housing_22_695bd157114f0d2317f88617: Missing tools: ['update_search_filter', 'search_apartments', 'calculate_commute']
- housing_24_65e8cf8f4c7424fa062e54a3: Missing tools: ['update_search_filter', 'search_apartments', 'calculate_commute']
- housing_24_69a9cf80f4d7668d5c815038: Missing tools: ['update_search_filter', 'search_apartments', 'calculate_commute']
- housing_25_66f59c766e7e22e1f90d08f6: Missing tools: ['update_search_filter', 'update_search_filter', 'search_apartments']
- travel_01_62a885d5b6af18b3d4579e1b: Missing tools: ['search_flights']
- travel_02_5e3c1fbece3a7b000a6fd95a: Wrong arguments for: ['update_identity_doc']
- travel_03_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_flights']
- travel_03_61517db6a7589569521b2356: Missing tools: ['search_flights']
- travel_05_6998abd731d2ec50d067d5bd: Missing tools: ['search_flights']
- travel_07_5ff07b5ee7a1d23e719e421e: Missing tools: ['update_identity_doc']
- travel_07_61517db6a7589569521b2356: Missing tools: ['update_identity_doc']
- travel_08_695bd157114f0d2317f88617: Missing tools: ['search_flights']
- travel_10_5f4a4da1575d605c43bef871: Missing tools: ['search_flights']
- travel_11_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_flights', 'book_flight']
- travel_14_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_flights']
- travel_16_66f59c766e7e22e1f90d08f6: Missing tools: ['search_flights']
- travel_18_66f59c766e7e22e1f90d08f6: Missing tools: ['search_flights', 'book_flight', 'update_identity_doc']
- travel_19_695bd157114f0d2317f88617: Wrong arguments for: ['search_flights']
- travel_20_62a885d5b6af18b3d4579e1b: Missing tools: ['search_flights', 'book_flight', 'update_identity_doc']
- travel_21_65e8cf8f4c7424fa062e54a3: Missing tools: ['search_flights', 'book_flight', 'update_identity_doc']
- travel_21_69a9cf80f4d7668d5c815038: Missing tools: ['search_flights', 'book_flight', 'update_identity_doc']
- travel_23_65e8cf8f4c7424fa062e54a3: Missing tools: ['update_identity_doc', 'update_identity_doc']
- travel_23_69a9cf80f4d7668d5c815038: Missing tools: ['update_identity_doc', 'update_identity_doc']
- travel_24_695bd157114f0d2317f88617: Unexpected tools: ['search_apartments']
