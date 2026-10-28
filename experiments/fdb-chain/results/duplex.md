## Full-duplex probe: tool calls in MiniCPM-o's duplex stream

| prompt | items | emits <tool_call> | call text that would reach TTS without the filter | after the filter |
| --- | --- | --- | --- | --- |
| fdb-native | 10 | 0 | 0 | 0 |
| fdb-delegate-strict | 10 | 0 | 0 | 0 |

Examples:

- fdb-native ecommerce_01_65e8cf8f4c7424fa062e54a3: raw: ' I will track your order using the ID you provided.<|turn_eos|>'
- fdb-native ecommerce_17_5f4a4da1575d605c43bef871: raw: " I need to check the current exchange rate for you. Please provide the amount, from_currency , and to_currency so I can fetch the accurate conversion.<|turn_eos|> I'll need to process a billing modification request. Coul"
- fdb-native finance_10_66c4f3cb14cbfc4db836bd4e: raw: " I'll look up the benefits for the travel card and check the current exchange rate for your currency conversion.<|turn_eos|>"
- fdb-native housing_06_5f4a4da1575d605c43bef871: raw: ''
- fdb-native travel_01_62a885d5b6af18b3d4579e1b: raw: ' I can help with that request. However, it appears my available tools are currently not functional. Would you like me to proceed by providing general guidance on'
- fdb-delegate-strict ecommerce_01_65e8cf8f4c7424fa062e54a3: raw: ' I will track your order with the ID ABC123 for you.<|turn_eos|>'
- fdb-delegate-strict ecommerce_17_5f4a4da1575d605c43bef871: raw: " I'm ready to assist you with your request. What would you like me to do?<|turn_eos|>"
- fdb-delegate-strict finance_10_66c4f3cb14cbfc4db836bd4e: raw: ''
- fdb-delegate-strict housing_06_5f4a4da1575d605c43bef871: raw: " I need to determine the user's request and call the appropriate tool. Since the user hasn ’t provided any specific query or context, I should prompt them for more information. However , according to the instructions, I "
- fdb-delegate-strict travel_01_62a885d5b6af18b3d4579e1b: raw: ' I can help you with that. Could you please provide me with your departure city and any preferred airline or class of service?<|turn_eos|>'
