## funaudio-fdb-delegate (exact match), $RESULT references resolved

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 27/100 (27%) |
| pass rate, benchmark errors counted as passed | 37/100 (37%) |
| tool selection F1 (mean) | 0.706 |
| argument accuracy (mean) | 0.595 |
| items with a handoff | 100/100 (100%) |
| FDB calls made by frontend / backend | 0 / 367 |
| unrelated harness tool calls | 0 |
| errors | 0 |
| end to end, median (s) | 11.43 |

| difficulty | pass rate |
| --- | --- |
| easy | 11/36 (31%) |
| medium | 12/34 (35%) |
| hard | 4/30 (13%) |
| self-correction items | 7/17 (41%) |

Failures:

- ecommerce_02_5f4a4da1575d605c43bef871: Unexpected tools: ['search_products', 'search_products', 'search_products']
- ecommerce_05_695bd157114f0d2317f88617: Unexpected tools: ['search_products', 'search_products', 'search_products']
- ecommerce_08_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['search_products', 'search_products', 'search_products']
- ecommerce_08_61517db6a7589569521b2356: Unexpected tools: ['search_products', 'search_products', 'search_products']
- ecommerce_10_6998abd731d2ec50d067d5bd: Unexpected tools: ['search_products']
- ecommerce_12_6998abd731d2ec50d067d5bd: Unexpected tools: ['search_products', 'search_products', 'search_products']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart', 'track_order']
- ecommerce_13_61517db6a7589569521b2356: Unexpected tools: ['add_to_cart', 'track_order', 'add_to_cart', 'track_order', 'add_to_cart', 'track_order']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Unexpected tools: ['search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order']
- ecommerce_20_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['search_products', 'add_to_cart', 'search_products', 'add_to_cart', 'search_products', 'add_to_cart']
- ecommerce_21_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart']
- ecommerce_21_69a9cf80f4d7668d5c815038: Unexpected tools: ['track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart']
- ecommerce_23_5f4a4da1575d605c43bef871: Unexpected tools: ['add_to_cart']
- ecommerce_25_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['search_products', 'add_to_cart']
- ecommerce_25_69a9cf80f4d7668d5c815038: Unexpected tools: ['search_products', 'add_to_cart']
- finance_01_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_01_69a9cf80f4d7668d5c815038: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_02_6998abd731d2ec50d067d5bd: Unexpected tools: ['get_card_benefits']
- finance_04_62a885d5b6af18b3d4579e1b: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_08_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_08_69a9cf80f4d7668d5c815038: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_10_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['get_card_benefits', 'get_exchange_rate', 'get_card_benefits', 'get_exchange_rate', 'get_card_benefits', 'get_exchange_rate']
- finance_12_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_12_69a9cf80f4d7668d5c815038: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_14_5f4a4da1575d605c43bef871: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_18_6998abd731d2ec50d067d5bd: Unexpected tools: ['get_exchange_rate', 'modify_autopay', 'get_card_benefits', 'get_exchange_rate', 'modify_autopay', 'get_card_benefits', 'get_exchange_rate', 'modify_autopay', 'get_card_benefits']
- finance_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['modify_autopay']; Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_21_5e3c1fbece3a7b000a6fd95a: Unexpected tools: ['get_card_benefits', 'get_exchange_rate', 'get_card_benefits', 'get_exchange_rate', 'get_card_benefits', 'get_exchange_rate']
- housing_01_62a885d5b6af18b3d4579e1b: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_03_5f4a4da1575d605c43bef871: Wrong arguments for: ['update_search_filter']
- housing_04_695bd157114f0d2317f88617: Unexpected tools: ['search_apartments']
- housing_05_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_05_61517db6a7589569521b2356: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_06_5f4a4da1575d605c43bef871: Unexpected tools: ['calculate_commute', 'calculate_commute', 'calculate_commute']
- housing_08_66f59c766e7e22e1f90d08f6: Unexpected tools: ['search_apartments']
- housing_10_66f59c766e7e22e1f90d08f6: Unexpected tools: ['calculate_commute', 'update_search_filter', 'calculate_commute', 'update_search_filter', 'calculate_commute', 'update_search_filter']
- housing_11_62a885d5b6af18b3d4579e1b: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_13_5f4a4da1575d605c43bef871: Wrong arguments for: ['update_search_filter', 'update_search_filter']
- housing_14_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['update_search_filter', 'search_apartments', 'update_search_filter', 'search_apartments', 'update_search_filter', 'search_apartments', 'update_search_filter']
- housing_14_61517db6a7589569521b2356: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_15_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_apartments']
- housing_15_61517db6a7589569521b2356: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_17_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['calculate_commute']
- housing_17_69a9cf80f4d7668d5c815038: Unexpected tools: ['calculate_commute', 'calculate_commute', 'calculate_commute']
- housing_18_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['search_apartments']
- housing_19_62a885d5b6af18b3d4579e1b: Wrong arguments for: ['calculate_commute']
- housing_20_62a885d5b6af18b3d4579e1b: Missing tools: ['update_search_filter']; Unexpected tools: ['search_apartments', 'search_apartments', 'calculate_commute', 'search_apartments', 'search_apartments', 'calculate_commute', 'search_apartments', 'search_apartments', 'calculate_commute', 'search_apartments']
- housing_21_66c4f3cb14cbfc4db836bd4e: Missing tools: ['calculate_commute']; Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_22_695bd157114f0d2317f88617: Unexpected tools: ['update_search_filter', 'search_apartments', 'calculate_commute', 'update_search_filter', 'search_apartments', 'calculate_commute', 'update_search_filter', 'search_apartments', 'calculate_commute']
- housing_24_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['update_search_filter', 'search_apartments', 'calculate_commute', 'update_search_filter', 'search_apartments', 'calculate_commute', 'update_search_filter', 'search_apartments', 'calculate_commute']
- housing_24_69a9cf80f4d7668d5c815038: Unexpected tools: ['update_search_filter', 'search_apartments', 'calculate_commute', 'update_search_filter', 'search_apartments', 'calculate_commute', 'update_search_filter', 'search_apartments', 'calculate_commute']
- housing_25_66f59c766e7e22e1f90d08f6: Unexpected tools: ['update_search_filter']
- travel_01_62a885d5b6af18b3d4579e1b: Unexpected tools: ['search_flights', 'search_flights', 'search_flights']
- travel_02_5e3c1fbece3a7b000a6fd95a: Wrong arguments for: ['update_identity_doc']
- travel_03_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_flights']
- travel_03_61517db6a7589569521b2356: Wrong arguments for: ['search_flights']
- travel_05_6998abd731d2ec50d067d5bd: Unexpected tools: ['search_flights', 'search_flights', 'search_flights']
- travel_07_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['update_identity_doc']
- travel_07_61517db6a7589569521b2356: Wrong arguments for: ['update_identity_doc']
- travel_08_695bd157114f0d2317f88617: Wrong arguments for: ['search_flights']
- travel_10_5f4a4da1575d605c43bef871: Wrong arguments for: ['search_flights']
- travel_11_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_flights']
- travel_14_66c4f3cb14cbfc4db836bd4e: Wrong arguments for: ['search_flights']
- travel_16_66f59c766e7e22e1f90d08f6: Unexpected tools: ['book_flight']
- travel_18_66f59c766e7e22e1f90d08f6: Unexpected tools: ['update_identity_doc', 'book_flight']
- travel_19_695bd157114f0d2317f88617: Unexpected tools: ['search_flights', 'search_flights', 'search_flights']
- travel_20_62a885d5b6af18b3d4579e1b: Missing tools: ['book_flight']; Unexpected tools: ['search_flights', 'update_identity_doc', 'search_flights', 'update_identity_doc', 'search_flights', 'update_identity_doc']
- travel_21_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['search_flights', 'book_flight', 'update_identity_doc', 'search_flights', 'book_flight', 'update_identity_doc', 'search_flights', 'book_flight', 'update_identity_doc']
- travel_21_69a9cf80f4d7668d5c815038: Unexpected tools: ['update_identity_doc']
- travel_23_65e8cf8f4c7424fa062e54a3: Wrong arguments for: ['update_identity_doc']
- travel_23_69a9cf80f4d7668d5c815038: Wrong arguments for: ['update_identity_doc']
- travel_24_695bd157114f0d2317f88617: Missing tools: ['search_flights']; Unexpected tools: ['book_flight', 'update_identity_doc']
