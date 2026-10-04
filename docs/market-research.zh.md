# 市场调研：AI 导购版时尚抢购

> 调研日期：2026-10-04。对象：「时尚闪购 + AI 导购 + LLM 保护层（语义缓存、按 token 限流、自动降级、熔断）」这个组合方案。

## 一句话结论

**方向可行，能覆盖 PDF 的全部要求，但要诚实地调整两点卖点：**

1. 单独的「秒杀防超卖」和单独的「LLM 网关（缓存 + 熔断 + 限流）」在 GitHub 上都已经很多了，而且不少已经带 k6 压测。所以之前说「网上项目都没有压测证明」不完全对。
2. 真正的空白在于**业务感知**：通用 LLM 网关只保护「一次 LLM 调用」，不知道「下单比聊天重要」。本项目的亮点应该定为：**大促时系统自动牺牲 AI 体验、优先保住下单链路，并且用数据证明。**

PDF 原文也说了 "We are not evaluating the idea itself"，他们看的是架构、取舍、自动化、测试和怎么用 AI。所以不用追求点子新，而要追求**做得扎实、跑得起来、讲得清楚**。

---

## 1. PDF 要求逐条对照

| PDF 要求 | 方案能否覆盖 | 需要注意的地方 |
|---|---|---|
| 有深度的系统设计，不是简单脚本 | ✅ 多个模块：商品/搜索、抢购、LLM 保护层、监控 | 要在 README 里画架构图、写取舍 |
| 体现 DevOps/SRE **自动化** | ⚠️ 部分覆盖 | 合并方案去掉了「运维助手」。建议补一个**一键演示脚本**：启动 → 压测 → 注入 LLM 故障 → 自动生成对比报告。这比聊天式运维助手更省时间，也更像 SRE 工作 |
| 计算机基础 | ✅ | 原子操作/并发、令牌桶、熔断状态机、向量相似度，都是好讲的点 |
| 换一台机器能跑起来（Docker Compose） | ⚠️ 有风险 | **评审没有你的 LLM API Key**。必须内置一个「模拟 LLM」作为默认，可配置延迟和故障率；有 Key 时再切到真实模型。这个模拟器本身也是演示熔断的工具 |
| 必须用 Claude Code 作为主要开发工具 | ✅ | 现在这个线程就是 Claude Code |
| 完整 commit 历史，不能 squash，不能只有几个大 commit | ⚠️ 要刻意做 | 每完成一个小功能就提交一次，PR 合并时用普通 merge，不要 squash |
| 必须有测试 | ✅ | 单元测试（令牌桶、熔断器、缓存）、集成测试（API + Redis）、**并发正确性测试**（证明不超卖） |
| AI_WORKFLOW.md（100–150 行） | ⚠️ 要边做边记 | 要写「AI 帮到的例子」和「AI 输出需要纠正的例子」各 1–2 个。建议开发过程中随手记下来，最后才写会想不起来 |
| 提交运行说明和测试说明 | ✅ | README 里写 `docker compose up` 和 `make test` |
| 面试演示：架构、代码、取舍、演进过程 | ✅ | 压测前后对比数据就是最好的演示材料 |

**结论：全部要求都能覆盖。** 唯一要补的是「自动化」这一项，建议用一键演示/故障注入脚本来补。

---

## 2. 现有产品和开源项目

### 2.1 秒杀 / 闪购项目（非常多）
- [ElijahSean/FlashSale](https://github.com/ElijahSean/FlashSale)：Redis Lua 防超卖 + BullMQ + Postgres，已经用 k6 测过 1 万用户。
- [LijuanTang94/flashdeal](https://github.com/LijuanTang94/flashdeal)：Java 21，Redis Lua + RabbitMQ + k6 压测 + SQL 对账。
- [tidi-dev/high-concurrency-flash-sale](https://github.com/tidi-dev/high-concurrency-flash-sale)：教学向，对比「直接扣数据库」和「Redis 预扣 + 队列」。

**结论：** 这一块只能当作基础，不能当亮点。好处是方案成熟，照着做风险低。

### 2.2 LLM 网关 / 保护层（也已经很多）
- 成熟产品：[LiteLLM](https://docs.litellm.ai/docs/proxy/reliability)（失败切换、冷却、按 TPM/预算限流、缓存）、Portkey、[GPTCache](https://github.com/zilliztech/GPTCache)（语义缓存库）、[Redis 语义缓存](https://redis.io/blog/how-to-cache-semantic-search/)。
- GitHub 个人项目：[aqkprogrammer/llm-gateway](https://github.com/aqkprogrammer/llm-gateway)（熔断 + 预算 + Redis 令牌桶 + Prometheus/Grafana）、[amaanmithani/modelmux](https://github.com/amaanmithani/modelmux)、[saiyasaswinimajety/enterprise-llm-gateway](https://github.com/saiyasaswinimajety/enterprise-llm-gateway) 等。

**结论：** 语义缓存、熔断、token 限流这些组件本身不新。但它们都是**通用网关**，不知道背后是什么业务。

### 2.3 真实行业案例（证明问题是真的）
- 亚马逊 Rufus 为了扛住 Prime Day，用了 [8 万多块 Inferentia/Trainium 芯片](https://aws.amazon.com/blogs/machine-learning/scaling-rufus-the-amazon-generative-ai-powered-conversational-shopping-assistant-with-over-80000-aws-inferentia-and-aws-trainium-chips-for-prime-day/)，还专门做了 [推理加速](https://aws.amazon.com/blogs/machine-learning/how-rufus-doubled-their-inference-speed-and-handled-prime-day-traffic-with-aws-ai-chips-and-parallel-decoding/)。据报道 Rufus 在 2025 年带来了 [约 120 亿美元销售额](https://ppc.land/amazons-ai-shopping-assistant-drove-12-billion-in-sales-for-2025/)。
- 这说明「大促 + AI 导购，LLM 是最贵最慢的环节」是真实的工程问题，面试时可以直接引用。

---

## 3. 这个项目真正的空白（建议的卖点）

1. **业务优先级降级：** 抢购开始时，下单链路优先；AI 导购按负载自动从「LLM 推荐」降到「向量搜索」再到「关键词搜索」。用户永远能用，只是体验分级。通用网关做不到这一点，因为它不知道哪个请求更重要。
2. **语义缓存的正确性问题：** 「50 美元以内的海边裙子」和「500 美元以内的海边裙子」语义非常像，但答案应该不同。直接用相似度缓存会返回错误结果（业界叫 false hit，见 [这篇](https://www.truefoundry.com/blog/semantic-caching-llm-gateway)）。做法是：先从问题里提取硬条件（价格、品类），作为缓存 key 的一部分，语义相似只用在剩下的部分。这是一个很好讲的工程取舍，也是计算机基础。
3. **用数据证明：** 同一个压测脚本跑两次（关保护 / 开保护），给出 P99 延迟、LLM 成本、缓存命中率、超卖数量的对比。

---

## 4. 两天、不太难的范围建议

### 建议技术选型（越少越好）
- **语言：Python + FastAPI**。AI 写 Python 最稳，测试也方便。
- **存储：** Redis（库存、限流、缓存）+ Postgres（订单）。先不上消息队列，用 Redis 原子扣减后直接写库就够了，面试时说明「为什么没加队列」本身就是取舍。
- **商品数据：** Hugging Face 上的 [Qdrant/hm_ecommerce_products](https://huggingface.co/datasets/Qdrant/hm_ecommerce_products)：约 10.6 万件 H&M 商品，带图片链接和**已经算好的向量**（BGE-small），CC BY 4.0 许可。只取 2000–5000 件就够，向量直接放内存或 pgvector，不需要训练任何模型。
- **LLM：** 默认用模拟 LLM（可调延迟、故障），可选接 Claude API。
- **前端：** 一个简单页面：搜索框 + 商品卡片 + 抢购按钮 + 显示当前降级级别。
- **监控：** Prometheus + Grafana，配好一个看板。
- **压测：** k6。

### 建议砍掉的
- 运维聊天助手（用一键演示脚本代替）
- 用户注册登录、支付
- 多个 LLM 供应商切换
- Kubernetes

### 两天排期（每一步都能单独提交和演示）
| 时间 | 内容 |
|---|---|
| 第 1 天上午 | 项目骨架、Docker Compose、导入商品、向量搜索 |
| 第 1 天下午 | 抢购接口、防超卖、幂等下单、并发正确性测试 |
| 第 2 天上午 | LLM 保护层：语义缓存、token 令牌桶、熔断、降级 + 单元测试 |
| 第 2 天下午 | k6 压测、Grafana 看板、一键演示脚本、README、AI_WORKFLOW.md |

### 风险
- **时间：** PDF 写的是「收到后 3 天内提交」。如果是 10 月 2 日收到的，截止就是明天（10 月 5 日）。请确认截止时间，必要时把第 2 天下午的看板降为「只出压测报告」。
- **评审跑不起来：** 用模拟 LLM 默认值 + 一条命令启动来解决。
- **commit 历史：** 从第一个 commit 开始就小步提交。
