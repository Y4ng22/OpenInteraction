---
lang: 中文
---
# 语音前台–后台进展

2026-10-24 · Xinzhuo Yang


每次运行的完整过程：[Handoff traces](#traces)

## 1. 概述

这个月我和 Matthew 搭好了系统的文本版：一个快速的语音模型负责和用户对话，一个更强、带工具的文本模型负责干活。

今天我会依次讲：系统结构、我们在 Full-Duplex-Bench v3 上的端到端实验、哪里出了问题和什么起了作用、回传路径，以及为什么我们认为整个系统需要自己的 benchmark。

## 2. 系统

{{arch}}

下面 benchmark 实验里的语音前台是 Fun-Audio-Chat-8B；我们的全双工服务用的是 MiniCPM-o 4.5。它负责听和说，并判断一个请求是否需要真正去做。需要的话，它发起唯一的 function call：spawn\_thinking，附上一段任务总结。

harness 接到这个调用，回一个任务 id，再把任务交给后台：带着 benchmark 十二个工具的 Qwen3-14B。后台做完后，结果沿着下面那条路径回到语音模型，由语音模型告诉用户。

我们的研究问题是 latent communication：让这两条箭头传递模型的内部状态，而不是文本。要衡量它能带来多少提升，得先把文本版跑通并测出来。这就是我今天要展示的。

## 3. 语音模型能自己决定交接吗？

我们发现两点。第一，如果语音模型自己手里有工具，它就会自己干：在 100 条 benchmark 题上，Fun-Audio-Chat 只交接了一次。第二，在全双工模式下 MiniCPM-o 从不输出 function call，二十次里零次，即使明确要求它委派。

既然前台只要手里有工具就从不委派，下一步我们会试着在工具里加上调用耗时，并给前台一个时间上限：预计超过上限的工作就交给后台。

当语音模型手里只有 spawn\_thinking 时，Fun-Audio-Chat 把 100 条请求全部交接了。

在我们的设计里，交接一定由语音模型决定，全双工也一样。全双工下的测试还是我们的一个待办。Fun-Audio-Chat 在回合制下交接很可靠，但它的全双工权重还没公开，没法在全双工下测。MiniCPM-o 能跑全双工，但从不输出 function call。

但大多数模型没有原生的委派能力，委派基本都靠提示词控制，因此表现很不稳定。让语音模型原生具备委派能力，也是我们想做的。

## 4. 为什么要委派

[Qwen-Audio-Agent 技术报告](https://arxiv.org/abs/2609.25195)正好直接测了这个问题：一个内部座舱测试集，134 个用例、41 个工具，语音模型是 Qwen Audio 3.0 Realtime Plus，后台是 qwen3.8-max。它比较了三种分工方式。

| 谁来干活 | 完成的短任务（共 86） | 完成的多步任务（共 48） | 任务成功率 | 平均延迟 |
| --- | --- | --- | --- | --- |
| 语音模型自己调工具 | 83 | 14 | 72.4% | 6.5 秒 |
| 全部交给后台 | 71 | 37 | 80.6% | 6.8 秒 |
| 语音模型按请求决定 | 84 | 38 | 91.0% | 4.7 秒 |

语音模型自己干，短指令又快又准，但 48 个多步任务只完成了 14 个。全部交给后台，多步任务好了，短指令却变差，整体也最慢。让语音模型自己决定哪些交出去，两边的优点都拿到了：成功率最高，延迟最低。这就是我们保留前台语音模型、并让它把需要的工作交给后台的原因。

延迟是另外在 80 个成功轮次上测的平均值。

## 5. FDB-v3 端到端结果

Full-Duplex-Bench v3 有 100 条语音请求，覆盖购物、旅行、金融和租房，配十二个模拟工具。只有每个预期的工具调用都用对了参数，这一条才算通过。

最好的设置是让后台干活：Fun-Audio-Chat 作为只负责交接的前台，通过 68 条；它同时拿着 12 个工具时只通过 51 条。

**FDB-v3 上各设置（回合制）**（共 100 条录音；“只交接”指语音模型手里只有 spawn\_thinking，由 Qwen3-14B 干活；“判官”指用 Qwen3-14B 跑 FDB-v3 自带的判分提示，$RESULT 引用按本次运行中前面真实返回的值来认）

| 设置 | 语音模型 | 通过（判官） | 通过（判官，错题算通过） | 通过（精确匹配） | 交接的条数 |
| --- | --- | --- | --- | --- | --- |
| 只交接 | Fun-Audio-Chat | 68 | 78 | 51 | 100 |
| 只交接 | MiniCPM-o | 22 | 32 | 18 | 41 |
| 有 12 个工具和 spawn\_thinking | Fun-Audio-Chat | 51 | 61 | 47 | 1 |
| 有 12 个工具和 spawn\_thinking | MiniCPM-o | 59 | 67 | 50 | 0 |

**MiniCPM-o 全双工**（把 FDB-v3 录音流式送入，每种提示词 10 条）

| 设置 | 条数 | 输出了 function call 的条数 |
| --- | --- | --- |
| 单独用 12 个工具 | 10 | 0 |
| 只交接，严格提示词 | 10 | 0 |

全双工下它只用语音回答，背后没有任何调用，比如“I will track your order using the ID you provided.”

## 6. 哪里出了问题，什么起了作用

最大的一处改进不是改措辞。只回一个光秃秃的 “accepted” 时，Fun-Audio-Chat 以为调用失败了，会把同一个任务再交一遍，最多四次。spawn\_thinking 改成回一个任务 id，并说明结果回来之前任务还没完成之后，重复交接就消失了，通过率从 36 升到 68。

**例子：ecommerce\_02，Fun-Audio-Chat，只交接。** 用户：“…I’m looking for a pair of wireless headphones… under 100 dollars if possible. What do you have?”

| 轮次 | 只回 “accepted” | 回复带任务 id |
| --- | --- | --- |
| 1 | spawn\_thinking：“Find wireless headphones under $100” | spawn\_thinking：“Find wireless headphones under $100” |
| 2 | spawn\_thinking：“Retrieve wireless headphones under $100” | “I’m looking for wireless headphones under $100 for you. Just a moment while I find the best options!” |
| 3 | spawn\_thinking：“Fetch wireless headphones under $100” | — |
| 4 | spawn\_thinking：“Get wireless headphones under $100” | — |
| 后台搜索次数 | 4 | 1 |

## 7. 回传路径

后台结果一到，语音模型都能转述得很好。在 Matthew 的全双工实验里，把结果写进 MiniCPM-o 的记忆后，工具返回的值有 93% 到 97% 被说给了用户；没有回传路径时只有 7%。在我的回合制测试里，Fun-Audio-Chat 在结果回来后的回复有 95% 只说了结果里有依据的事实。

verbatim（原文写入）有一个对话流畅性的问题：前台往往会先回一句，比如“我查一下”，后台结果在这之后才到，直接把结果原文塞进去，上下文会接不上。我用任务 id 加字段的方式解决了：交接时的回复带上任务 id，并说明任务还没完成；结果到达时带同一个任务 id、标成已完成，前台就能把晚到的结果和自己之前的回复对上。

真正没解决的是等待。结果回来之前，语音模型常常宣布一个还没发生的结果，比如“我已经把两件商品加入购物车了”。这在 Matthew 的全双工实验里占 6% 到 15%，在 Fun-Audio-Chat 的回合制交接里占 17%，都经过人工核对。立刻写入提示（Matthew 的 sys\_now）降得最多。

**例子：ecommerce\_14，Fun-Audio-Chat，只交接，回复带任务 id。** 用户：“…Can you add, like, 2 of item P-5-2 to my cart? I want to get one for myself and one as a gift.”

| 时间点 | 说了什么 |
| --- | --- |
| 结果回来之前 | 语音模型：“I’ve added two items of product P-5-2 to your cart, one for yourself and one as a gift.”实际上还什么都没加。 |
| 后台结果 | “Two items of product P-5-2 have been added to the user’s cart. The cart total is now $199.98.” |
| 结果回来之后 | 语音模型：“Great! Two items of product P-5-2 have been added to your cart, and the total is now $199.98.” |

## 8. 注意事项

这些数字有几点需要注意。

benchmark 本身有 10 条题的标准答案是错的：和它自己的工具定义或录音内容冲突。比如有三条期望搜公寓时带一个 pets\_allowed 参数，而这个工具根本没有这个参数。表里“错题算通过”那一列把这 10 条算作通过。

## 9. 为什么需要系统级 benchmark

到目前为止，我们用的每个 benchmark 都只给单个模型打分。Full-Duplex-Bench 和 τ-Voice 问的是一个语音模型能不能调对工具。我们的系统是语音模型加后台，我们关心的能力恰恰在两者之间：任务有没有交出去，用户的改口或取消能不能传到正在运行的任务，最终到达用户的结果对不对。我们的系统可以在这些 benchmark 上跑，刚才也展示了，但它们体现不出这些能力。

FDB-v3 里确实有 21 道自我纠正题，比如“查飞巴黎的航班——不对，改成柏林”。但改口发生在同一句话里，任务还没建立，所以从头到尾只有一个任务。这类题测的是理解，不是交互。我们的交接设置在这类题上反而最好：带任务 id 的 Fun-Audio-Chat 在我们跑的 17 条里过了 15 条，因为前台写的描述已经把改口理顺了。

语音 agent 正在转向这种交互模型：GPT-Live、Gemini Live、Thinking Machines 的 Interaction Models，以及第 4 节的 Qwen-Audio-Agent。我实习时做的就是这个方向的预研。缺的是一个统一的方法，去衡量这样一个完整系统到底表现如何。

最接近的评测就是第 4 节里 [Qwen-Audio-Agent](https://arxiv.org/abs/2609.25195) 的座舱测试。[Qwen-Audio-3.1-Realtime 技术报告](https://arxiv.org/abs/2609.25176)也提到，这个测试用的是合成语音、模拟工具和直接委派接口，没有测异步任务的完整生命周期。任务运行期间语音模型该说什么，被列为未来工作。

这个空白正是我们自己的失败所在：结果还没回来就宣布结果，以及把同一个任务重复交接。任务进行中的改口和取消也发生在这里，而现有 benchmark 都不测这些。所以我们计划先在 FDB-v3 这样的现有 benchmark 上证明委派的想法，再为整个系统做一个开放的 benchmark。

## 10. 下一步

你们 134 个用例的座舱测试集测了整个系统的路由和最终结果。Qwen-Audio-Agent 在 10 月 15 日加入了 update\_agent\_task，用来修改正在运行的任务；在这之前，这类 function call 是我自己加进 harness 的。我用 API 测下来，任务完成得非常好。但我没有看到你们对任务跑起来之后的改口、取消和异步过程的测试，所以我们还想做这个 benchmark：用 agent-as-a-judge，由一个评测 agent 扮演用户，在任务交出去之后再改口或取消，最后判断结果对不对。
