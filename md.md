╔════════════════════════════════════════════════════════════╗
║        机器学习影子学习器 - 详细使用说明                   ║
╚════════════════════════════════════════════════════════════╝


【一、文件说明】
=====================
原有文件（不用动）：
  严格选股_多周期.py  - 你的选股器
  stock_analyzer.py   - 你的分析器
  data_source.py      - 你的数据源
  stock_list.md       - 你的股票列表

新增文件（需要创建）：
  shadow_learner.py    - 核心：存数据、训练模型
  enhanced_screener.py - 入口：你每天运行这个
  weekly_train.py      - 训练：每周五运行

自动生成的文件：
  shadow_data.pkl      - 存你的选股记录
  shadow_model.pkl     - 训练好的模型


【二、文件1：shadow_learner.py - 核心逻辑】
=====================
这个文件是大脑，负责所有数据存储和机器学习

主要函数：
  __init__()           - 启动时加载已有的数据和模型
  record_signal()      - 记录一条选股信号（存所有指标）
  update_outcomes()    - 5天后更新实际结果（涨没涨）
  train()              - 训练模型，分析哪些指标重要
  predict_success_prob()- 预测成功率
  get_stats()          - 查看统计数据

你不用直接运行这个文件，它被其他文件调用


【三、文件2：enhanced_screener.py - 你每天用的】
=====================
这个文件是你每天选股用的，和原来操作一样

只需要改一处：
  把原来的 from 严格选股_多周期 import ...
  改成 from 严格选股_多周期 import ...

主要函数：
  on_signal()          - 当选到信号时，自动调用分析并记录
  main()               - 主菜单（和原来一样）

每天运行：
  python enhanced_screener.py
  然后正常选股（1-单独测试，2-批量筛选）


【四、文件3：weekly_train.py - 每周五运行】
=====================
这个文件每周五收盘后运行一次，更新结果并训练

主要函数：
  main()               - 先更新结果，再训练模型

每周五运行：
  python weekly_train.py

它会：
  1. 查看所有未更新的记录
  2. 获取当前价格，判断是否达标
  3. 如果有50条以上达标数据，开始训练
  4. 输出特征重要性（哪些指标最有用）


【五、完整使用流程】
=====================

第1步：创建文件
  把3个文件都放到你的项目文件夹

第2-4周：收集数据
  每天运行：python enhanced_screener.py
  正常选股，程序会自动记录信号

第5周周五：第一次训练
  运行：python weekly_train.py
  它会更新前4周的结果，然后训练模型

第6周开始：每天选股时会显示
  【机器学习预测成功率: 78%】

以后每周五：
  python weekly_train.py  # 持续优化模型


【六、常用查看命令】
=====================

查看数据量：
  python -c "import pickle; data=pickle.load(open('shadow_data.pkl','rb')); print(f'已收集{len(data)}条')"

查看信号类型统计：
  python -c "
import pickle
data=pickle.load(open('shadow_data.pkl','rb'))
stats={}
for r in data:
    st=r.get('signal_type','普通')
    stats[st]=stats.get(st,0)+1
print('信号类型:',stats)
"

查看最新5条：
  python -c "
import pickle
data=pickle.load(open('shadow_data.pkl','rb'))
for r in data[-5:]:
    print(f\"{r['code']} {r['signal_type']} 评分:{r['final_score']} 目标:{r['target_price']}\")
"

查看训练后的特征重要性：
  python -c "from shadow_learner import show_feature_importance; show_feature_importance()"


【七、注意事项】
=====================
1. 5天逻辑：选到的股票，5天后才看结果，给足时间

2. 50条门槛：至少要有50条有结果的数据才能训练

3. 信号类型：会自动记录 严格/筑底/突破/普通

4. 不影响原系统：所有逻辑都不改你的文件


【八、就这么简单！】
=====================
✅ 每天：python enhanced_screener.py
✅ 每周五：python weekly_train.py
✅ 想看结果：用上面的查看命令