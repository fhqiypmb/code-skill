"""
影子学习器 - 自动记录信号数据 + 定期训练
数据格式：JSON，方便追踪和调试
去重逻辑：同一天 + 同股票 + 同周期 + 同信号类型 只写一条
"""

import os
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# 文件路径（相对于本文件所在的 ml/ 目录）
_ML_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_FILE  = os.path.join(_ML_DIR, 'shadow_data.json')
MODEL_FILE = os.path.join(_ML_DIR, 'shadow_model.pkl')
POTENTIAL_MODEL_FILE = os.path.join(_ML_DIR, 'shadow_potential_model.pkl')
GAIN_MODEL_FILE = os.path.join(_ML_DIR, 'shadow_gain_model.pkl')

# 多少个交易日后回填实际结果
OUTCOME_DAYS = 5

# 训练所需的最少已标记样本数
MIN_TRAIN_SAMPLES = 45

# 十倍/百倍早期潜力模型：在现有5日样本内，先学习“高弹性爆发”标签。
# 当前数据源没有财务/市值/估值，不能直接学习真正10倍/100倍；
# 先用“5日内最大涨幅 >= 8%”作为早期可验证代理标签。
POTENTIAL_GAIN_THRESHOLD_PCT = 8.0

# 涨幅模型：预测5日内最大涨幅 ≥8% 的概率（全特征、不校准、纯排序）
GAIN_THRESHOLD_PCT = 8.0


# ==================== 数据读写 ====================

def _load_data() -> List[Dict]:
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f"加载ML数据: {len(data)} 条")
        return data
    except Exception as e:
        logger.warning(f"ML数据加载失败: {e}")
        return []


def _save_data(data: List[Dict], auto_push: bool = False) -> None:
    os.makedirs(_ML_DIR, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"ML数据已保存: {len(data)} 条")

    # 本地环境自动 push（Action 由 Commit signals 步骤统一提交）
    if auto_push and not _is_ci():
        try:
            import subprocess
            repo_root = os.path.dirname(os.path.dirname(_ML_DIR))
            subprocess.run(['git', 'add', DATA_FILE], cwd=repo_root, timeout=10)
            subprocess.run(
                ['git', 'commit', '-m', f'ML数据自动更新 {datetime.now().strftime("%Y-%m-%d %H:%M")}'],
                cwd=repo_root, capture_output=True, timeout=10
            )
            subprocess.run(['git', 'push'], cwd=repo_root, capture_output=True, timeout=30)
            logger.info("ML数据已自动 push 到远端")
        except Exception as e:
            logger.warning(f"ML数据自动 push 失败（数据已保存本地）: {e}")


# ==================== 去重 ====================

def _dedup_key(date: str, code: str, period: str, signal_type: str) -> str:
    return f"{date}|{code}|{period}|{signal_type}"


def _is_duplicate(data: List[Dict], date: str, code: str, period: str, signal_type: str) -> bool:
    key = _dedup_key(date, code, period, signal_type)
    for record in data:
        if _dedup_key(
            record.get('date', ''),
            record.get('code', ''),
            record.get('period', ''),
            record.get('signal_type', '')
        ) == key:
            return True
    return False


# ==================== 核心：记录信号 ====================

def _pull_and_merge() -> List[Dict]:
    """
    本地环境：git pull 拿最新 shadow_data.json，与本地合并后返回
    Action 环境或 git 不可用时：直接返回本地数据
    去重key = date|code|period|signal_type
    """
    local_data = _load_data()

    # 判断是否在 git 仓库里、是否有 git 命令
    try:
        import subprocess
        repo_root = os.path.dirname(os.path.dirname(_ML_DIR))  # stocks/ml -> stocks -> repo根
        result = subprocess.run(
            ['git', 'pull', '--rebase', '--autostash'],
            cwd=repo_root,
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.warning(f"git pull 失败（忽略）: {result.stderr.strip()}")
            return local_data
        logger.info(f"git pull 完成: {result.stdout.strip()}")
    except Exception as e:
        logger.debug(f"git pull 跳过: {e}")
        return local_data

    # pull 之后重新读（可能有远端新数据）
    remote_data = _load_data()

    # 以远端为基准，把本地独有的条目合并进去
    existing_keys = {
        _dedup_key(r.get('date',''), r.get('code',''), r.get('period',''), r.get('signal_type',''))
        for r in remote_data
    }
    added = 0
    for r in local_data:
        k = _dedup_key(r.get('date',''), r.get('code',''), r.get('period',''), r.get('signal_type',''))
        if k not in existing_keys:
            remote_data.append(r)
            existing_keys.add(k)
            added += 1

    if added:
        logger.info(f"本地合并: 新增 {added} 条到远端数据")
        _save_data(remote_data)

    return remote_data


def _is_ci() -> bool:
    """判断是否在 CI/Action 环境"""
    return os.environ.get('CI') == 'true' or os.environ.get('GITHUB_ACTIONS') == 'true'


def record_signal(
    code: str,
    name: str,
    period: str,
    signal_type: str,
    screener_details: Dict,
    analysis: Dict,
) -> bool:
    """
    记录一次信号到 shadow_data.json
    analysis 直接复用已跑完的结果，不重复请求
    本地环境：写入前先 git pull 合并，避免覆盖 Action 写的数据
    返回 True=新记录写入，False=重复跳过
    """
    today = datetime.now().strftime('%Y-%m-%d')

    # CI 环境：checkout 拿到的可能是旧版本，需要与当前文件合并（避免覆盖本地已有数据）
    # 本地环境：git pull 拿最新数据再合并
    data = _load_data() if _is_ci() else _pull_and_merge()

    # 去重：同一天同股票同周期同信号类型只写一次
    if _is_duplicate(data, today, code, period, signal_type):
        logger.info(f"ML去重跳过: {today} {code} {period} {signal_type}")
        # 返回已有记录供预测使用
        for r in data:
            if _dedup_key(r.get('date',''), r.get('code',''), r.get('period',''), r.get('signal_type','')) == _dedup_key(today, code, period, signal_type):
                return r
        return None

    # 打平 screener_details 的数值型字段
    screener_feats = {}
    for k, v in screener_details.items():
        if isinstance(v, (int, float, bool)):
            screener_feats[f'sc_{k}'] = float(v) if isinstance(v, bool) else v

    # 打平 analysis 的所有字段（递归）
    analysis_feats = {}
    def _flatten(d: Dict, prefix: str = 'an'):
        for k, v in d.items():
            key = f'{prefix}_{k}'
            if isinstance(v, dict):
                _flatten(v, key)
            elif isinstance(v, (int, float, bool)):
                analysis_feats[key] = float(v) if isinstance(v, bool) else v
            elif isinstance(v, str) and v:
                analysis_feats[key] = v
    _flatten(analysis)

    sr   = analysis.get('success_rate', {})
    tech = analysis.get('technical', {})

    record = {
        # 基础标识
        'date':        today,
        'code':        code,
        'name':        name,
        'period':      period,
        'signal_type': signal_type,
        'timestamp':   time.time(),

        # 信号快照
        'close':           screener_details.get('close', 0),
        'gold_cross_date': screener_details.get('gold_cross_date', ''),
        'confirm_date':    screener_details.get('date', ''),

        # 分析结果快照
        'verdict':      analysis.get('verdict', ''),
        'industry':     analysis.get('industry', ''),
        'sr_score':     sr.get('score', 0),
        'sr_grade':     sr.get('grade', ''),
        'target_price': tech.get('target_price', 0),
        'stop_loss':    tech.get('stop_loss', 0),

        # 打平的特征（供训练用）
        **screener_feats,
        **analysis_feats,

        # 实际结果（后续回填）
        'reached_target': None,
        'actual_return':  None,
        'exit_price':     None,
        'exit_date':      None,

        # 标注哪些字段是整合/派生得到的（训练时已排除，仅供参考）
        '_derived_fields': {
            'sr_score':                         '= an_success_rate_score 的快照冗余',
            'an_success_rate_score':            '= dim_breakout*0.22 + dim_momentum*0.22 + dim_rs*0.18 + dim_capital*0.20 + dim_rr*0.10 + dim_reach_prob*0.08',
            'an_success_rate_dim_reach_prob':   '= breakout_rate*0.4 + rr_factor*0.35 + vol_factor*0.25',
            'an_success_rate_dim_rr':           '= an_technical_expected_gain_pct / an_technical_stop_loss_pct 的分档映射',
            'an_success_rate_dim_momentum':     '= an_trend_score*0.7 + an_trend_macd_strength*0.3',
            'an_success_rate_dim_rs':           '= an_market_pos_rs_score（完全相同）',
            'an_trend_score':                   '= trend_detail_ma_align*0.30 + trend_detail_vol_price*0.40 + trend_detail_macd*0.30',
            'an_market_pos_score':              '= an_market_pos_rs_score*0.5 + an_market_pos_vr_score*0.5',
            'an_market_pos_rs_score':           '= an_market_pos_relative_strength 的分档打分',
            'an_market_pos_vr_score':           '= an_market_pos_vol_ratio 的分档打分',
            'an_technical_expected_gain_pct':   '= (target_price - current_price) / current_price',
            'an_technical_stop_loss_pct':       '= (stop_loss - current_price) / current_price',
            'an_technical_space_ok':            '= an_technical_expected_gain_pct >= 10.0',
            'an_technical_target_price':        '= 压力位法/ATR通道法/斐波那契 三者取中位数',
            'target_price':                     '= an_technical_target_price 的快照冗余',
            'an_technical_ma20':                '= sc_ma20（完全相同）',
            'close':                            '= sc_close（完全相同）',
            'an_quote_price':                   '= sc_close（完全相同）',
            'an_technical_current_price':       '= sc_close（完全相同）',
            'stop_loss':                        '= an_technical_stop_loss（完全相同）',
        },
    }

    data.append(record)
    _save_data(data, auto_push=not _is_ci())
    logger.info(f"ML记录: {today} {code} {name} [{period}][{signal_type}] 共{len(record)}个字段")
    return record


# ==================== 回填实际结果 ====================

def update_outcomes() -> int:
    """
    回填信号发出后 OUTCOME_DAYS 天的实际结果
    只处理：reached_target 为 None 且距信号日已超过 OUTCOME_DAYS 天的记录
    """
    try:
        import sys
        stocks_dir = os.path.dirname(_ML_DIR)
        if stocks_dir not in sys.path:
            sys.path.insert(0, stocks_dir)
        import data_source
    except ImportError as e:
        logger.error(f"导入data_source失败: {e}")
        return 0

    data    = _load_data()
    updated = 0
    cutoff  = (datetime.now() - timedelta(days=OUTCOME_DAYS)).strftime('%Y-%m-%d')

    for record in data:
        if record.get('reached_target') is not None:
            continue
        if record.get('date', '9999') > cutoff:
            continue

        code         = record['code']
        entry_price  = record.get('close', 0)
        target_price = record.get('target_price', 0)
        signal_date  = record.get('date', '')

        if not entry_price or not target_price:
            continue

        try:
            # 拉取信号日之后足够多的K线，筛选出信号日之后的5个交易日
            klines = data_source.fetch_kline(code, period='240min', limit=30)
            if not klines:
                continue

            # 只取信号日之后的K线（不含信号日当天）
            after_klines = [k for k in klines if k.get('day', k.get('date', '')) > signal_date]
            if len(after_klines) < OUTCOME_DAYS:
                continue  # 交易日不足5天，跳过

            # 取信号日之后的前5个交易日
            window = after_klines[:OUTCOME_DAYS]

            # 用窗口内最高价判断是否触达目标价
            max_high = max(float(k.get('high', k.get('close', 0))) for k in window)
            # exit_price 仍记录第5个交易日的收盘价（反映持有到期收益）
            exit_price = float(window[-1].get('close', 0))
            if not exit_price:
                continue

            actual_return = (exit_price - entry_price) / entry_price
            max_gain_pct  = (max_high - entry_price) / entry_price * 100
            reached       = 1 if max_high >= target_price else 0

            record['reached_target'] = reached
            record['actual_return']  = round(actual_return, 4)
            record['exit_price']     = exit_price
            record['max_high']       = max_high
            record['max_gain_pct']   = round(max_gain_pct, 2)
            record['exit_date']      = window[-1].get('day', window[-1].get('date', ''))
            updated += 1

            logger.info(
                f"回填 {code}: 入{entry_price:.2f} 目标{target_price:.2f} "
                f"期间最高{max_high:.2f} 末日{exit_price:.2f} "
                f"{'达标' if reached else '未达标'} ({actual_return*100:.1f}%)"
            )
            time.sleep(0.3)

        except Exception as e:
            logger.warning(f"回填失败 {code}: {e}")

    if updated:
        _save_data(data)
    logger.info(f"回填完成: {updated} 条")
    return updated


# ==================== 训练 ====================

def train() -> Optional[Any]:
    """训练随机森林模型，返回模型对象，失败返回 None

    优化点：
    1. 时间序列切分（前80%训练 / 后20%测试），避免未来信息泄漏
    2. 去掉 class_weight='balanced'，让模型输出真实概率分布
    3. 用 CalibratedClassifierCV(isotonic) 做概率校准，确保输出概率≈实际达标率
    """
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.metrics import accuracy_score, classification_report
        import joblib
    except ImportError as e:
        logger.error(f"训练依赖缺失: {e}，请安装: pip install scikit-learn joblib numpy")
        return None

    data    = _load_data()
    labeled = [r for r in data if r.get('reached_target') is not None]

    if len(labeled) < MIN_TRAIN_SAMPLES:
        logger.warning(f"已标记样本不足: {len(labeled)} < {MIN_TRAIN_SAMPLES}，跳过训练")
        return None

    # 时间序列切分需要先按日期升序排序
    labeled.sort(key=lambda r: r.get('date', ''))

    # 排除元信息和结果字段，只保留数值型特征
    exclude = {
        'date', 'code', 'name', 'period', 'signal_type', 'timestamp',
        'gold_cross_date', 'confirm_date', 'verdict', 'industry',
        'sr_grade', 'exit_date', 'reached_target', 'actual_return', 'exit_price', 'max_high', 'max_gain_pct', 'max_gain_pct',
        # 排除综合评分/加权合成字段（它们是子参数的二次加工，会干扰模型对底层因子的学习）
        'sr_score',                         # = an_success_rate_score 的冗余快照
        'an_success_rate_score',            # 6个子维度的加权求和
        'an_success_rate_dim_reach_prob',   # 多因子加权合成的到达概率
        'an_success_rate_dim_rr',           # expected_gain / stop_loss 的分档映射，与子字段重复
        'an_success_rate_dim_momentum',     # trend_score * 0.7 + macd_strength * 0.3
        'an_success_rate_dim_rs',           # 直接等于 market_pos.rs_score，完全重复
        'an_trend_score',                   # ma_score*0.3 + vp_score*0.4 + macd_score*0.3
        'an_market_pos_score',              # rs_score*0.5 + vr_score*0.5
        'an_market_pos_rs_score',           # relative_strength 的分档打分，与原始值重复
        'an_market_pos_vr_score',           # vol_ratio 的分档打分，与原始值重复
        # 排除派生/冗余的技术指标字段
        'an_technical_expected_gain_pct',   # (target - price) / price，由子字段可推出
        'an_technical_stop_loss_pct',       # (stop - price) / price，由子字段可推出
        'an_technical_space_ok',            # expected_gain >= 10 的布尔映射
        'an_technical_target_price',        # 压力位/ATR/斐波那契三法取中位数，整合结果
        'target_price',                     # 同上，record层冗余快照
        'an_technical_ma20',                # 与 sc_ma20 重复
        # 排除重复的价格字段（同一个值存了多份，只保留一个）
        'close',                            # 与 sc_close 相同
        'an_quote_price',                   # 与 sc_close 相同
        'an_technical_current_price',       # 与 sc_close 相同
        'stop_loss',                        # 与 an_technical_stop_loss 相同
        # 排除ML预测快照字段（仅供查阅，不参与训练）
        'ml_predict_prob',                  # 当时的ML预测概率
        'ml_predict_gain',                  # 当时的ML预测涨幅
        'ml_top3_features',                 # 当时模型的TOP3特征名
        # 排除高度冗余的特征（实测相关系数 > 0.99，会稀释特征重要性）
        'sc_ma30',                          # 与 sc_ma20 相关系数 0.9998
        'sc_first_double_price',            # 与 sc_close 相关系数 0.9995
        'an_quote_high',                    # 与 sc_close 高度相关
        'an_quote_low',                     # 与 sc_close 高度相关
        'an_quote_open',                    # 与 sc_close 高度相关
        'an_quote_pre_close',               # 与 sc_close 高度相关
        'an_technical_method_targets_压力位法',   # 与 target_price 高度相关（合成时使用）
        'an_technical_method_targets_ATR通道法',  # 与 target_price 高度相关（合成时使用）
        'an_technical_method_targets_斐波那契',   # 与 target_price 高度相关（合成时使用）
    }
    feature_fields = sorted({
        k for r in labeled for k, v in r.items()
        if k not in exclude and isinstance(v, (int, float))
    })

    logger.info(f"训练样本: {len(labeled)} 条，特征: {len(feature_fields)} 个")

    X = np.array([[r.get(f, 0) or 0 for f in feature_fields] for r in labeled], dtype=float)
    y = np.array([r['reached_target'] for r in labeled], dtype=int)

    logger.info(f"正样本比例(达标): {y.mean():.2%}")

    # 时间序列切分：前80%训练，后20%测试，模拟"用历史预测未来"
    n_split = int(len(labeled) * 0.8)
    X_train, X_test = X[:n_split], X[n_split:]
    y_train, y_test = y[:n_split], y[n_split:]
    train_end_date = labeled[n_split - 1].get('date', '?')
    test_start_date = labeled[n_split].get('date', '?')
    logger.info(f"时间序列切分: 训练截止 {train_end_date}, 测试起始 {test_start_date}")
    logger.info(f"训练集 {len(y_train)} ({y_train.mean():.2%}正类) | 测试集 {len(y_test)} ({y_test.mean():.2%}正类)")

    # 1) 基础随机森林（不再用 class_weight='balanced'，让模型输出真实分布）
    base_model = RandomForestClassifier(
        n_estimators=100, max_depth=10,
        min_samples_split=5, min_samples_leaf=2,
        random_state=42, n_jobs=-1,
    )
    # 2) 用 isotonic 5折交叉校准，让输出概率≈实际达标率
    model = CalibratedClassifierCV(base_model, method='isotonic', cv=5)
    model.fit(X_train, y_train)

    train_acc = accuracy_score(y_train, model.predict(X_train))
    test_acc  = accuracy_score(y_test,  model.predict(X_test))
    logger.info(f"训练集准确率: {train_acc:.2%}  测试集准确率: {test_acc:.2%}")
    logger.info(f"\n{classification_report(y_test, model.predict(X_test))}")

    test_probs = model.predict_proba(X_test)[:, 1] * 100
    prob_bucket_stats = _calc_probability_buckets(test_probs, y_test)
    logger.info("测试集概率桶命中率:")
    for s in prob_bucket_stats:
        logger.info(
            f"  {s['label']}: {s['total']}个信号，命中{s['hit']}个，命中率{s['hit_rate']:.1%}"
        )

    # CalibratedClassifierCV 内部有 cv=5 个 base estimator，取平均特征重要性
    all_imps = np.array([
        cc.estimator.feature_importances_
        for cc in model.calibrated_classifiers_
    ])
    avg_imp = all_imps.mean(axis=0)
    importance = sorted(
        zip(feature_fields, avg_imp),
        key=lambda x: x[1], reverse=True
    )
    logger.info("特征重要性 TOP20:")
    for i, (fname, imp) in enumerate(importance[:20], 1):
        logger.info(f"  {i:2d}. {fname:<45} {imp:.4f}")

    bundle = {
        'model':         model,
        'feature_names': feature_fields,
        'importance':    importance,
        'train_acc':     train_acc,
        'test_acc':      test_acc,
        'prob_bucket_stats': prob_bucket_stats,
        'train_date':    datetime.now().strftime('%Y-%m-%d'),
        'sample_count':  len(labeled),
        'train_end_date':  train_end_date,
        'test_start_date': test_start_date,
        'split_method':    'time_series',  # 时间序列切分
        'calibrated':      True,           # 已做 isotonic 校准
    }
    joblib.dump(bundle, MODEL_FILE)
    logger.info(f"模型已保存: {MODEL_FILE}")

    # 同时训练十倍/百倍早期潜力模型（用高弹性爆发作为当前可验证代理标签）
    potential_bundle = _train_potential_model(labeled, feature_fields)

    # 训练涨幅排序模型（全特征、不校准、标签统一为 ≥8%）
    gain_bundle = _train_gain_model(labeled)

    _save_report(bundle, labeled, y, feature_fields, potential_bundle, gain_bundle)

    return model


def _calc_probability_buckets(probabilities, labels) -> List[Dict]:
    """按实战阈值统计测试集概率桶命中率"""
    buckets = [
        (">=40%", lambda p: p >= 40),
        ("35%-40%", lambda p: 35 <= p < 40),
        ("30%-35%", lambda p: 30 <= p < 35),
        ("25%-30%", lambda p: 25 <= p < 30),
        ("<25%", lambda p: p < 25),
    ]
    stats = []
    for label, predicate in buckets:
        indexes = [i for i, p in enumerate(probabilities) if predicate(float(p))]
        total = len(indexes)
        hit = int(sum(int(labels[i]) for i in indexes)) if total else 0
        stats.append({
            'label': label,
            'total': total,
            'hit': hit,
            'hit_rate': hit / total if total else 0,
        })
    return stats


# ==================== 十倍/百倍早期潜力模型 ====================

def _train_potential_model(labeled: List[Dict], feature_fields: List[str]) -> Optional[Dict]:
    """训练早期潜力模型。

    当前数据源缺少市值、估值、营收、利润、ROE、现金流、研发、机构持仓等长期因子，
    不能直接给“真实十倍/百倍概率”打标签。因此这里使用现有样本中可回测、可验证的
    代理目标：信号后5个交易日内最大涨幅 >= POTENTIAL_GAIN_THRESHOLD_PCT。

    它的作用不是替代短线分类模型，而是辅助判断“上涨空间/后续爆发潜力”：
    短线达标概率负责确定性，潜力概率负责弹性空间。
    """
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.metrics import accuracy_score, classification_report
        import joblib
    except ImportError as e:
        logger.error(f"潜力模型依赖缺失: {e}")
        return None

    potential_data = [r for r in labeled if r.get('max_gain_pct') is not None]
    if len(potential_data) < MIN_TRAIN_SAMPLES:
        logger.warning(f"潜力模型样本不足: {len(potential_data)} < {MIN_TRAIN_SAMPLES}，跳过")
        return None

    X = np.array([[r.get(f, 0) or 0 for f in feature_fields] for r in potential_data], dtype=float)
    y = np.array([
        1 if float(r.get('max_gain_pct') or 0) >= POTENTIAL_GAIN_THRESHOLD_PCT else 0
        for r in potential_data
    ], dtype=int)

    if len(set(y.tolist())) < 2:
        logger.warning("潜力模型只有单一类别，跳过训练")
        return None

    # 时间序列切分（与分类模型保持一致）
    n_split = int(len(potential_data) * 0.8)
    X_train, X_test = X[:n_split], X[n_split:]
    y_train, y_test = y[:n_split], y[n_split:]

    if len(set(y_train.tolist())) < 2:
        logger.warning("潜力模型训练集只有单一类别，跳过训练")
        return None

    base_model = RandomForestClassifier(
        n_estimators=100, max_depth=10,
        min_samples_split=5, min_samples_leaf=2,
        random_state=42, n_jobs=-1,
    )
    model = CalibratedClassifierCV(base_model, method='isotonic', cv=5)
    model.fit(X_train, y_train)

    train_acc = accuracy_score(y_train, model.predict(X_train))
    test_acc  = accuracy_score(y_test,  model.predict(X_test))
    logger.info(
        f"[潜力] 标签=max_gain_pct>={POTENTIAL_GAIN_THRESHOLD_PCT:.1f}% "
        f"正样本比例: {y.mean():.2%}"
    )
    logger.info(f"[潜力] 训练集准确率: {train_acc:.2%}  测试集准确率: {test_acc:.2%}")
    logger.info(f"\n{classification_report(y_test, model.predict(X_test))}")

    test_probs = model.predict_proba(X_test)[:, 1] * 100
    prob_bucket_stats = _calc_potential_buckets(test_probs, y_test)
    logger.info("潜力模型概率桶命中率:")
    for s in prob_bucket_stats:
        logger.info(
            f"  {s['label']}: {s['total']}个信号，命中{s['hit']}个，命中率{s['hit_rate']:.1%}"
        )

    all_imps = np.array([
        cc.estimator.feature_importances_
        for cc in model.calibrated_classifiers_
    ])
    avg_imp = all_imps.mean(axis=0)
    importance = sorted(
        zip(feature_fields, avg_imp),
        key=lambda x: x[1], reverse=True
    )

    bundle = {
        'model':         model,
        'feature_names': feature_fields,
        'importance':    importance,
        'train_acc':     train_acc,
        'test_acc':      test_acc,
        'prob_bucket_stats': prob_bucket_stats,
        'train_date':    datetime.now().strftime('%Y-%m-%d'),
        'sample_count':  len(potential_data),
        'positive_rate': round(float(y.mean()), 4),
        'gain_threshold_pct': POTENTIAL_GAIN_THRESHOLD_PCT,
        'model_role':    'tenbagger_seed_auxiliary',
        'label_desc':    f'信号后{OUTCOME_DAYS}个交易日内最大涨幅 >= {POTENTIAL_GAIN_THRESHOLD_PCT:.1f}%',
    }
    joblib.dump(bundle, POTENTIAL_MODEL_FILE)
    logger.info(f"潜力模型已保存: {POTENTIAL_MODEL_FILE}")
    return bundle


# ==================== 涨幅排序模型（第三个模型）====================

def _train_gain_model(labeled: List[Dict]) -> Optional[Dict]:
    """训练涨幅排序模型。

    与短线达标模型的核心区别：
    1. 标签：max_gain_pct >= GAIN_THRESHOLD_PCT（统一标准，非个股目标价）
    2. 特征：全量特征（含 sr_score 等派生字段，实战验证有效）
    3. 模型：纯 RandomForest，不做 isotonic 校准（保持分数区分度）

    目标不是输出"精准概率"，而是输出一个可靠的排序分数，
    让实盘可以按分数取 Top-N 信号买入。
    """
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import accuracy_score, classification_report
        import joblib
    except ImportError as e:
        logger.error(f"涨幅模型依赖缺失: {e}")
        return None

    gain_data = [r for r in labeled if r.get('max_gain_pct') is not None]
    if len(gain_data) < MIN_TRAIN_SAMPLES:
        logger.warning(f"涨幅模型样本不足: {len(gain_data)} < {MIN_TRAIN_SAMPLES}，跳过")
        return None

    # 全量特征（不排除派生字段）：只排除元信息、结果字段、旧模型预测
    exclude = {
        'date', 'code', 'name', 'period', 'signal_type', 'timestamp',
        'gold_cross_date', 'confirm_date', 'verdict', 'industry',
        'sr_grade', 'exit_date',
        'reached_target', 'actual_return', 'exit_price', 'max_high', 'max_gain_pct',
        'ml_predict_prob', 'ml_predict_gain', 'ml_predict_potential', 'ml_top3_features',
        # 排除高度冗余的完全重复字段
        'close', 'an_quote_price', 'an_technical_current_price',
        'an_technical_ma20', 'stop_loss',
        'an_technical_method_targets_压力位法',
        'an_technical_method_targets_ATR通道法',
        'an_technical_method_targets_斐波那契',
    }
    feature_fields = sorted({
        k for r in gain_data for k, v in r.items()
        if k not in exclude and isinstance(v, (int, float))
    })

    logger.info(f"[涨幅模型] 样本: {len(gain_data)} 条，全量特征: {len(feature_fields)} 个（含 sr_score 等派生字段）")

    X = np.array([[r.get(f, 0) or 0 for f in feature_fields] for r in gain_data], dtype=float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    y = np.array([
        1 if float(r.get('max_gain_pct') or 0) >= GAIN_THRESHOLD_PCT else 0
        for r in gain_data
    ], dtype=int)

    if len(set(y.tolist())) < 2:
        logger.warning("涨幅模型只有单一类别，跳过训练")
        return None

    logger.info(f"[涨幅模型] 正样本比例(≥{GAIN_THRESHOLD_PCT:.1f}%): {y.mean():.2%}")

    # 时间序列切分
    gain_data_sorted = sorted(gain_data, key=lambda r: r.get('date', ''))
    n_split = int(len(gain_data_sorted) * 0.8)
    X_sorted = np.array([[r.get(f, 0) or 0 for f in feature_fields] for r in gain_data_sorted], dtype=float)
    X_sorted = np.nan_to_num(X_sorted, nan=0.0, posinf=0.0, neginf=0.0)
    y_sorted = np.array([
        1 if float(r.get('max_gain_pct') or 0) >= GAIN_THRESHOLD_PCT else 0
        for r in gain_data_sorted
    ], dtype=int)

    X_train, X_test = X_sorted[:n_split], X_sorted[n_split:]
    y_train, y_test = y_sorted[:n_split], y_sorted[n_split:]

    if len(set(y_train.tolist())) < 2:
        logger.warning("涨幅模型训练集只有单一类别，跳过训练")
        return None

    # 纯 RF，不做校准
    model = RandomForestClassifier(
        n_estimators=100, max_depth=10,
        min_samples_split=5, min_samples_leaf=2,
        random_state=42, n_jobs=-1,
    )
    model.fit(X_train, y_train)

    train_acc = accuracy_score(y_train, model.predict(X_train))
    test_acc = accuracy_score(y_test, model.predict(X_test))
    logger.info(f"[涨幅模型] 训练集准确率: {train_acc:.2%}  测试集准确率: {test_acc:.2%}")
    logger.info(f"\n{classification_report(y_test, model.predict(X_test))}")

    # 特征重要性
    importance = sorted(
        zip(feature_fields, model.feature_importances_),
        key=lambda x: x[1], reverse=True
    )
    logger.info("涨幅模型特征重要性 TOP15:")
    for i, (fname, imp) in enumerate(importance[:15], 1):
        logger.info(f"  {i:2d}. {fname:<45} {imp:.4f}")

    # Top-N 命中率验证
    test_proba = model.predict_proba(X_test)[:, 1]
    test_order = np.argsort(test_proba)[::-1]
    logger.info("涨幅模型排序效果（测试集）:")
    for top_pct in [0.5, 0.3, 0.2, 0.1]:
        k = max(1, int(len(y_test) * top_pct))
        hit = y_test[test_order[:k]].mean()
        baseline = y_test.mean()
        logger.info(f"  Top{int(top_pct*100):2d}% ({k:3d}只): 命中率 {hit:.1%} (基线 {baseline:.1%}, 提升 {hit/baseline:.1f}x)")

    # 计算实战阈值
    full_proba = model.predict_proba(X_sorted)[:, 1]
    full_order = np.argsort(full_proba)[::-1]
    thresholds = {}
    for pct in [20, 25, 30]:
        k = max(1, int(len(full_proba) * pct / 100))
        thresholds[f'top{pct}_threshold'] = round(float(full_proba[full_order][k - 1]), 4)
    logger.info(f"[涨幅模型] 实战阈值: {thresholds}")

    bundle = {
        'model': model,
        'feature_names': feature_fields,
        'importance': importance,
        'train_acc': train_acc,
        'test_acc': test_acc,
        'train_date': datetime.now().strftime('%Y-%m-%d'),
        'sample_count': len(gain_data),
        'positive_rate': round(float(y.mean()), 4),
        'gain_threshold_pct': GAIN_THRESHOLD_PCT,
        'model_role': 'gain_ranking',
        'label_desc': f'信号后{OUTCOME_DAYS}个交易日内最大涨幅 >= {GAIN_THRESHOLD_PCT:.1f}%',
        'calibrated': False,
        'thresholds': thresholds,
    }
    joblib.dump(bundle, GAIN_MODEL_FILE)
    logger.info(f"涨幅模型已保存: {GAIN_MODEL_FILE}")
    return bundle


def predict_gain(record: Dict) -> Optional[float]:
    """用涨幅排序模型预测该信号的涨幅概率（排序分数，非严格概率）。

    返回 0~100 的分数，分数越高越可能 5 日内涨 ≥8%。
    实盘建议：取分数 ≥ 67（历史 Top20%）的信号买入。
    """
    if not os.path.exists(GAIN_MODEL_FILE):
        return None
    try:
        import joblib
        import numpy as np
        bundle = joblib.load(GAIN_MODEL_FILE)
        model = bundle['model']
        feature_names = bundle['feature_names']
        X = np.array([[record.get(f, 0) or 0 for f in feature_names]], dtype=float)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        prob = model.predict_proba(X)[0][1]
        return round(float(prob) * 100, 1)
    except Exception as e:
        logger.warning(f"涨幅模型预测失败: {e}")
        return None


def _calc_potential_buckets(probabilities, labels) -> List[Dict]:
    """按潜力阈值统计测试集命中率。"""
    buckets = [
        (">=60%", lambda p: p >= 60),
        ("50%-60%", lambda p: 50 <= p < 60),
        ("40%-50%", lambda p: 40 <= p < 50),
        ("30%-40%", lambda p: 30 <= p < 40),
        ("<30%", lambda p: p < 30),
    ]
    stats = []
    for label, predicate in buckets:
        indexes = [i for i, p in enumerate(probabilities) if predicate(float(p))]
        total = len(indexes)
        hit = int(sum(int(labels[i]) for i in indexes)) if total else 0
        stats.append({
            'label': label,
            'total': total,
            'hit': hit,
            'hit_rate': hit / total if total else 0,
        })
    return stats


# ==================== 特征名中文对照表（供查阅）====================
# 报告中字段名前缀说明：
#   sc_  = 选股指标（StockScreener）
#   an_quote_    = 实时行情
#   an_technical_= 技术指标
#   an_trend_    = 趋势强度
#   an_market_pos_ = 市场强度
#   an_success_rate_ = 成功率各维度
#   an_capital_  = 主力资金
FEATURE_NAMES_ZH = {
    # 选股指标 (sc_)
    'sc_close':              '收盘价',
    'sc_ma20':               'MA20均线',
    'sc_ma30':               'MA30均线',
    'sc_volume':             '成交量',
    'sc_days_since_gold':    '金叉距今天数',
    'sc_days_since_first_double': '倍量阳距今天数',
    'sc_first_double_price': '倍量阳收盘价',
    'sc_first_double_vol':   '倍量阳成交量',
    'sc_gold_day_vol':       '金叉日成交量',
    'sc_yin_vol':            '阴线成交量',
    'sc_gap_days':           '金叉到确认天数',
    'sc_ma5_rising':         'MA5上升',
    'sc_bottom_stable':      '底部稳定',
    'sc_vol_explode':        '成交量爆量',
    'sc_bottom_buy':         '筑底买入',
    'sc_breakout_buy':       '突破买入',
    # 行情 (an_quote_)
    'an_quote_price':        '当前价格',
    'an_quote_change_pct':   '涨跌幅%',
    'an_quote_high':         '今日最高',
    'an_quote_low':          '今日最低',
    'an_quote_open':         '今日开盘',
    'an_quote_pre_close':    '昨日收盘',
    'an_quote_volume':       '成交量(手)',
    'an_quote_amount':       '成交额(元)',
    'an_quote_turnover_rate':'换手率%',
    # 技术指标 (an_technical_)
    'an_technical_current_price':        '当前价',
    'an_technical_target_price':         '目标价',
    'an_technical_stop_loss':            '止损价',
    'an_technical_expected_gain_pct':    '预期涨幅%',
    'an_technical_stop_loss_pct':        '止损幅度%',
    'an_technical_space_ok':             '空间达标',
    'an_technical_atr':                  'ATR波动率',
    'an_technical_ma20':                 '技术MA20',
    'an_technical_method_targets_压力位法':  '目标价-压力位法',
    'an_technical_method_targets_ATR通道法': '目标价-ATR法',
    'an_technical_method_targets_斐波那契':  '目标价-斐波那契',
    # 趋势 (an_trend_)
    'an_trend_score':            '趋势总分',
    'an_trend_ma_align':         '均线多头排列',
    'an_trend_vol_price_ok':     '量价配合',
    'an_trend_macd_positive':    'MACD正值',
    'an_trend_macd_strength':    'MACD强度',
    'an_trend_detail_ma_align':  '趋势-均线得分',
    'an_trend_detail_vol_price': '趋势-量价得分',
    'an_trend_detail_macd':      '趋势-MACD得分',
    # 市场强度 (an_market_pos_)
    'an_market_pos_score':            '市场强度总分',
    'an_market_pos_relative_strength':'相对强度(vs基准)',
    'an_market_pos_rs_score':         '相对强度得分',
    'an_market_pos_vol_ratio':        '量比',
    'an_market_pos_vr_score':         '量比得分',
    # 成功率 (an_success_rate_)
    'an_success_rate_score':          '成功率总分',
    'an_success_rate_dim_breakout':   '成功率-突破维度',
    'an_success_rate_dim_momentum':   '成功率-动能维度',
    'an_success_rate_dim_rs':         '成功率-相对强度',
    'an_success_rate_dim_capital':    '成功率-资金维度',
    'an_success_rate_dim_rr':         '成功率-风险收益',
    'an_success_rate_dim_reach_prob': '到达目标价概率',
    # 资金 (an_capital_)
    'an_capital_main_net_in':  '主力净流入(万)',
    'an_capital_super_net_in': '超大单净流入(万)',
    'an_capital_big_net_in':   '大单净流入(万)',
    'an_capital_flow_ratio':   '主力流入强度%',
    'an_capital_confirmed':    '资金确认',
}


def _save_report(bundle: Dict, labeled: List[Dict], y, feature_fields: List[str], potential_bundle: Optional[Dict] = None, gain_bundle: Optional[Dict] = None) -> None:
    """生成模型分析报告 model_report.md"""
    report_file = os.path.join(_ML_DIR, 'model_report.md')
    train_date  = bundle['train_date']
    train_acc   = bundle['train_acc']
    test_acc    = bundle['test_acc']
    sample_count = bundle['sample_count']
    importance  = bundle['importance']

    lines = []
    lines.append(f"# ML模型分析报告")
    lines.append(f"\n> **训练日期**: {train_date}（每周一自动更新）  ")
    lines.append(f"> **共用样本数**: {sample_count}（已回填实际涨跌结果的历史信号数量）  ")
    lines.append(
        f"> 本系统包含两个独立模型：**短线达标分类模型**（预测能否触达目标价）和 "
        f"**十倍/百倍早期潜力模型**（预测高弹性爆发概率，当前代理标签为5日内最大涨幅>={POTENTIAL_GAIN_THRESHOLD_PCT:.1f}%）"
    )

    # ── 样本概况 ──
    lines.append(f"\n---\n## 一、样本概况")

    lines.append(f"\n### 按周期分布")
    lines.append("| 周期 | 总信号 | 达标数 | 达标率 | 高弹性数 | 高弹性率 |")
    lines.append("|------|--------|--------|--------|----------|----------|")
    from collections import defaultdict
    period_stats = defaultdict(lambda: {'total': 0, 'hit': 0, 'potential': 0})
    for r in labeled:
        p = r.get('period', '?')
        period_stats[p]['total'] += 1
        period_stats[p]['hit'] += r.get('reached_target', 0)
        period_stats[p]['potential'] += 1 if float(r.get('max_gain_pct') or 0) >= POTENTIAL_GAIN_THRESHOLD_PCT else 0
    for p, s in sorted(period_stats.items()):
        rate = s['hit'] / s['total'] if s['total'] else 0
        potential_rate = s['potential'] / s['total'] if s['total'] else 0
        lines.append(f"| {p} | {s['total']} | {s['hit']} | {rate:.1%} | {s['potential']} | {potential_rate:.1%} |")

    # ── 按信号类型达标率（筑底/突破/严格/普通）──
    lines.append(f"\n### 按信号类型分布")
    lines.append("信号类型说明：**筑底**=底部企稳反弹、**突破**=放量突破压力位、**严格**=金叉严格条件全满足、**普通**=金叉基本条件满足")
    lines.append("")
    lines.append("| 信号类型 | 总信号 | 达标数 | 达标率 | 高弹性数 | 高弹性率 |")
    lines.append("|----------|--------|--------|--------|----------|----------|")
    type_order = ['筑底', '突破', '严格', '普通']
    type_stats = defaultdict(lambda: {'total': 0, 'hit': 0, 'potential': 0})
    for r in labeled:
        t = r.get('signal_type', '?')
        type_stats[t]['total'] += 1
        type_stats[t]['hit'] += r.get('reached_target', 0)
        type_stats[t]['potential'] += 1 if float(r.get('max_gain_pct') or 0) >= POTENTIAL_GAIN_THRESHOLD_PCT else 0
    shown = []
    for t in type_order:
        if t in type_stats:
            s = type_stats[t]
            rate = s['hit'] / s['total'] if s['total'] else 0
            potential_rate = s['potential'] / s['total'] if s['total'] else 0
            lines.append(f"| {t} | {s['total']} | {s['hit']} | {rate:.1%} | {s['potential']} | {potential_rate:.1%} |")
            shown.append(t)
    for t, s in sorted(type_stats.items()):
        if t not in shown:
            rate = s['hit'] / s['total'] if s['total'] else 0
            potential_rate = s['potential'] / s['total'] if s['total'] else 0
            lines.append(f"| {t} | {s['total']} | {s['hit']} | {rate:.1%} | {s['potential']} | {potential_rate:.1%} |")

    # ── 分类模型 ──
    lines.append(f"\n---\n## 二、短线达标分类模型")
    lines.append(f"\n任务：预测信号发出后{OUTCOME_DAYS}个交易日内，最高价能否触达目标价。")
    lines.append(f"- **训练集准确率**: {train_acc:.2%}  |  **测试集准确率**: {test_acc:.2%}")

    if bundle.get('split_method') == 'time_series':
        lines.append(f"- **切分方式**: 时间序列切分（前80%训练 / 后20%测试，无未来信息泄漏）")
        lines.append(f"- **训练截止**: {bundle.get('train_end_date', '?')}  |  **测试起始**: {bundle.get('test_start_date', '?')}")
    if bundle.get('calibrated'):
        lines.append(f"- **概率校准**: 已使用 isotonic 5折交叉校准，模型输出概率 ≈ 实际达标率")
        lines.append("")
        lines.append(
            f"> 概率含义：短线达标概率衡量的是“先赚确定性的钱”。"
            f"整体基准达标率约 {sum(r.get('reached_target',0) for r in labeled)/len(labeled):.1%}，"
            f"实战建议重点关注概率 >= 40% 的信号。"
        )

    prob_bucket_stats = bundle.get('prob_bucket_stats', [])
    if prob_bucket_stats:
        lines.append(f"\n### 测试集概率桶命中率")
        lines.append("| 概率区间 | 信号数 | 命中数 | 命中率 |")
        lines.append("|----------|--------|--------|--------|")
        for s in prob_bucket_stats:
            lines.append(f"| {s['label']} | {s['total']} | {s['hit']} | {s['hit_rate']:.1%} |")

    lines.append(f"\n### 特征重要性 TOP20")
    lines.append("| 排名 | 特征名 | 重要性得分 |")
    lines.append("|------|--------|------------|")
    for i, (fname, imp) in enumerate(importance[:20], 1):
        lines.append(f"| {i} | `{fname}` | {imp:.4f} |")

    lines.append(f"\n### 达标 vs 未达标信号特征对比")
    import numpy as np
    hit_records  = [r for r in labeled if r.get('reached_target') == 1]
    miss_records = [r for r in labeled if r.get('reached_target') == 0]
    top_features = [f for f, _ in importance[:15]]
    lines.append("| 特征名 | 达标均值 | 未达标均值 | 差异 |")
    lines.append("|--------|----------|------------|------|")
    for fname in top_features:
        hit_vals  = [r.get(fname, 0) or 0 for r in hit_records]
        miss_vals = [r.get(fname, 0) or 0 for r in miss_records]
        hit_mean  = np.mean(hit_vals)  if hit_vals  else 0
        miss_mean = np.mean(miss_vals) if miss_vals else 0
        diff      = hit_mean - miss_mean
        direction = "↑达标更高" if diff > 0 else "↓未达标更高"
        lines.append(f"| `{fname}` | {hit_mean:.3f} | {miss_mean:.3f} | {diff:+.3f} {direction} |")

    # ── 十倍/百倍早期潜力模型 ──
    if potential_bundle:
        lines.append(f"\n---\n## 三、十倍/百倍早期潜力模型")
        lines.append(
            f"\n任务：在当前数据源较少的阶段，先学习“高弹性爆发”而不直接宣称真实十倍/百倍。"
            f"标签为：信号发出后{OUTCOME_DAYS}个交易日内最大涨幅 >= {potential_bundle.get('gain_threshold_pct', POTENTIAL_GAIN_THRESHOLD_PCT):.1f}%。"
        )
        lines.append(
            f"\n作用：给短线达标模型做辅助。短线模型负责确定性，潜力模型负责空间。"
            f"当短线概率一般但潜力概率高时，表示它可能不是马上涨，但具备更大的后续弹性；"
            f"当短线概率和潜力概率同时高时，才是“看长做短”的优先信号。"
        )
        lines.append(f"- **样本数**: {potential_bundle.get('sample_count', '?')}")
        lines.append(f"- **高弹性基准率**: {potential_bundle.get('positive_rate', 0):.1%}")
        lines.append(f"- **训练集准确率**: {potential_bundle.get('train_acc', 0):.2%}  |  **测试集准确率**: {potential_bundle.get('test_acc', 0):.2%}")
        p_stats = potential_bundle.get('prob_bucket_stats', [])
        if p_stats:
            lines.append(f"\n### 潜力概率桶命中率")
            lines.append("| 潜力概率区间 | 信号数 | 高弹性命中数 | 命中率 |")
            lines.append("|--------------|--------|--------------|--------|")
            for s in p_stats:
                lines.append(f"| {s['label']} | {s['total']} | {s['hit']} | {s['hit_rate']:.1%} |")
        p_imp = potential_bundle.get('importance', [])
        if p_imp:
            lines.append(f"\n### 潜力模型特征重要性 TOP20")
            lines.append("| 排名 | 特征名 | 重要性得分 |")
            lines.append("|------|--------|------------|")
            for i, (fname, imp) in enumerate(p_imp[:20], 1):
                lines.append(f"| {i} | `{fname}` | {imp:.4f} |")

        high_records = [r for r in labeled if float(r.get('max_gain_pct') or 0) >= POTENTIAL_GAIN_THRESHOLD_PCT]
        low_records = [r for r in labeled if float(r.get('max_gain_pct') or 0) < POTENTIAL_GAIN_THRESHOLD_PCT]
        top_p_features = [f for f, _ in p_imp[:15]]
        if top_p_features:
            lines.append(f"\n### 高弹性 vs 普通信号特征对比")
            lines.append("| 特征名 | 高弹性均值 | 普通均值 | 差异 |")
            lines.append("|--------|------------|----------|------|")
            for fname in top_p_features:
                high_vals = [r.get(fname, 0) or 0 for r in high_records]
                low_vals = [r.get(fname, 0) or 0 for r in low_records]
                high_mean = np.mean(high_vals) if high_vals else 0
                low_mean = np.mean(low_vals) if low_vals else 0
                diff = high_mean - low_mean
                direction = "↑高弹性更高" if diff > 0 else "↓普通更高"
                lines.append(f"| `{fname}` | {high_mean:.3f} | {low_mean:.3f} | {diff:+.3f} {direction} |")

    # ── 涨幅排序模型 ──
    if gain_bundle:
        lines.append(f"\n---\n## 四、涨幅排序模型（新·第三个模型）")
        lines.append(f"\n任务：纯排序模型，预测信号发出后{OUTCOME_DAYS}个交易日内最大涨幅 >= {gain_bundle.get('gain_threshold_pct', GAIN_THRESHOLD_PCT):.1f}% 的概率。")
        lines.append(f"\n与短线达标模型的核心区别：")
        lines.append(f"- 标签统一标准（≥8%），不依赖个股特定目标价")
        lines.append(f"- 特征全量（含 sr_score 等派生字段，实战验证有效）")
        lines.append(f"- 不做 isotonic 校准，保持分数区分度，专为排序设计")
        lines.append(f"- **样本数**: {gain_bundle.get('sample_count', '?')}")
        lines.append(f"- **正样本率**: {gain_bundle.get('positive_rate', 0):.1%}")
        lines.append(f"- **训练集准确率**: {gain_bundle.get('train_acc', 0):.2%}  |  **测试集准确率**: {gain_bundle.get('test_acc', 0):.2%}")
        thresholds = gain_bundle.get('thresholds', {})
        if thresholds:
            lines.append(f"\n### 实战阈值")
            lines.append("| 阈值 | 含义 |")
            lines.append("|------|------|")
            for k, v in sorted(thresholds.items()):
                pct = k.replace('top', '').replace('_threshold', '')
                lines.append(f"| >= {v} | 历史 Top{pct}% 信号 |")
        g_imp = gain_bundle.get('importance', [])
        if g_imp:
            lines.append(f"\n### 特征重要性 TOP15")
            lines.append("| 排名 | 特征名 | 重要性得分 |")
            lines.append("|------|--------|------------|")
            for i, (fname, imp) in enumerate(g_imp[:15], 1):
                lines.append(f"| {i} | `{fname}` | {imp:.4f} |")

    # ── 结论 ──
    lines.append(f"\n---\n## 五、结论摘要")
    top3 = [f for f, _ in importance[:3]]
    lines.append(f"- 短线达标模型最关键的3个特征: `{'` / `'.join(top3)}`")
    overall_rate = sum(r.get('reached_target', 0) for r in labeled) / len(labeled)
    potential_rate = sum(1 for r in labeled if float(r.get('max_gain_pct') or 0) >= POTENTIAL_GAIN_THRESHOLD_PCT) / len(labeled)
    lines.append(f"- 短线整体达标率: {overall_rate:.1%}（短线确定性基准线）")
    lines.append(f"- 高弹性整体命中率: {potential_rate:.1%}（潜力模型基准线）")
    lines.append(f"- 使用方式：优先选择 `短线达标概率高 + 潜力概率高`；若短线一般但潜力高，可降低仓位观察，等待分类模型或技术信号二次确认。")
    lines.append(f"- 当前限制：尚未接入市值、估值、财务成长、ROE、现金流、研发和机构持仓，因此该模型是“十倍股早期线索模型”，不是完整基本面十倍股模型。")

    with open(report_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    logger.info(f"模型报告已保存: {report_file}")


# ==================== 仅预测用的特征构造 ====================

def _build_predict_features(
    code: str,
    name: str,
    period: str,
    signal_type: str,
    screener_details: Dict,
    analysis: Dict,
) -> Dict:
    """
    构造与 record_signal 相同结构的特征字典，但不写入文件。
    仅用于本地手动选股时的 ML 预测。
    """
    from datetime import datetime as _dt
    today = _dt.now().strftime('%Y-%m-%d')

    # 打平 screener_details 的数值型字段
    screener_feats = {}
    for k, v in screener_details.items():
        if isinstance(v, (int, float, bool)):
            screener_feats[f'sc_{k}'] = float(v) if isinstance(v, bool) else v

    # 打平 analysis 的所有字段（递归）
    analysis_feats = {}
    def _flatten(d: Dict, prefix: str = 'an'):
        for k, v in d.items():
            key = f'{prefix}_{k}'
            if isinstance(v, dict):
                _flatten(v, key)
            elif isinstance(v, (int, float, bool)):
                analysis_feats[key] = float(v) if isinstance(v, bool) else v
            elif isinstance(v, str) and v:
                analysis_feats[key] = v
    _flatten(analysis)

    sr   = analysis.get('success_rate', {})
    tech = analysis.get('technical', {})

    record = {
        'date':        today,
        'code':        code,
        'name':        name,
        'period':      period,
        'signal_type': signal_type,
        'timestamp':   time.time(),
        'close':           screener_details.get('close', 0),
        'gold_cross_date': screener_details.get('gold_cross_date', ''),
        'confirm_date':    screener_details.get('date', ''),
        'verdict':      analysis.get('verdict', ''),
        'industry':     analysis.get('industry', ''),
        'sr_score':     sr.get('score', 0),
        'sr_grade':     sr.get('grade', ''),
        'target_price': tech.get('target_price', 0),
        'stop_loss':    tech.get('stop_loss', 0),
        **screener_feats,
        **analysis_feats,
    }
    return record


# ==================== 预测 ====================

def predict(record: Dict) -> Optional[float]:
    """
    用已训练的模型预测该信号的达标概率。
    模型不存在或预测失败返回 None，不影响主流程。
    record 的字段结构与 shadow_data.json 中的记录一致。
    """
    if not os.path.exists(MODEL_FILE):
        return None
    try:
        import joblib
        import numpy as np
        bundle = joblib.load(MODEL_FILE)
        model         = bundle['model']
        feature_names = bundle['feature_names']
        X = np.array([[record.get(f, 0) or 0 for f in feature_names]], dtype=float)
        prob = model.predict_proba(X)[0][1]  # 达标概率
        return round(float(prob) * 100, 1)
    except Exception as e:
        logger.warning(f"ML预测失败: {e}")
        return None


def predict_potential(record: Dict) -> Optional[float]:
    """
    用早期潜力模型预测该信号的高弹性爆发概率。
    当前含义：未来 OUTCOME_DAYS 个交易日内最大涨幅 >= POTENTIAL_GAIN_THRESHOLD_PCT 的概率。
    这是十倍/百倍早期线索模型，用于辅助短线分类模型，不直接等同真实十倍/百倍概率。
    """
    if not os.path.exists(POTENTIAL_MODEL_FILE):
        return None
    try:
        import joblib
        import numpy as np
        bundle = joblib.load(POTENTIAL_MODEL_FILE)
        model         = bundle['model']
        feature_names = bundle['feature_names']
        X = np.array([[record.get(f, 0) or 0 for f in feature_names]], dtype=float)
        prob = model.predict_proba(X)[0][1]
        return round(float(prob) * 100, 1)
    except Exception as e:
        logger.warning(f"ML潜力预测失败: {e}")
        return None


def _get_model_top3() -> List[str]:
    """获取当前模型的 TOP3 重要特征名，模型不存在返回空列表"""
    if not os.path.exists(MODEL_FILE):
        return []
    try:
        import joblib
        bundle = joblib.load(MODEL_FILE)
        importance = bundle.get('importance', [])
        return [name for name, _ in importance[:3]]
    except Exception:
        return []


def record_and_predict(
    code: str,
    name: str,
    period: str,
    signal_type: str,
    screener_details: Dict,
    analysis: Dict,
    save: bool = True,
) -> Dict:
    """
    记录信号 + 立即预测三个模型的概率，一步完成。
    返回 {'prob': 达标概率, 'potential': 高弹性潜力概率, 'gain': 涨幅排序分数}，
    模型不存在或失败时对应值为 None。

    参数:
        save: 是否将信号写入 shadow_data.json。
              True  → 记录+预测（GitHub Actions / monitor.py 使用）
              False → 仅预测不记录（本地手动选股使用，避免污染数据）
    """
    for attempt in range(1, 4):
        try:
            if save:
                record = record_signal(
                    code=code, name=name,
                    period=period, signal_type=signal_type,
                    screener_details=screener_details,
                    analysis=analysis,
                )
            else:
                record = _build_predict_features(
                    code=code, name=name,
                    period=period, signal_type=signal_type,
                    screener_details=screener_details,
                    analysis=analysis,
                )
            prob = predict(record) if record else None
            potential = predict_potential(record) if record else None
            gain = predict_gain(record) if record else None

            # 将ML预测结果回写到记录中（仅供查阅，不参与训练）
            if save and record and isinstance(record, dict):
                record['ml_predict_prob'] = prob
                record['ml_predict_potential'] = potential
                record['ml_predict_gain'] = gain
                record['ml_top3_features'] = _get_model_top3()
                data = _load_data()
                for r in reversed(data):
                    if (r.get('date') == record.get('date') and
                        r.get('code') == record.get('code') and
                        r.get('period') == record.get('period') and
                        r.get('signal_type') == record.get('signal_type')):
                        r['ml_predict_prob'] = prob
                        r['ml_predict_potential'] = potential
                        r['ml_predict_gain'] = gain
                        r['ml_top3_features'] = record['ml_top3_features']
                        break
                _save_data(data)

            return {'prob': prob, 'potential': potential, 'gain': gain}
        except Exception as e:
            import traceback
            logger.error(f"ML记录/预测失败 {code} (第{attempt}次): {e}\n{traceback.format_exc()}")
            if attempt < 3:
                import time
                time.sleep(1)
    return {'prob': None, 'potential': None, 'gain': None}


# ==================== 统计 ====================

def get_stats() -> Dict:
    data    = _load_data()
    labeled = [r for r in data if r.get('reached_target') is not None]
    acc     = sum(r['reached_target'] for r in labeled) / len(labeled) if labeled else 0
    by_period = {}
    for r in data:
        p = r.get('period', '?')
        by_period[p] = by_period.get(p, 0) + 1
    return {
        'total':        len(data),
        'labeled':      len(labeled),
        'unlabeled':    len(data) - len(labeled),
        'accuracy':     round(acc, 4),
        'by_period':    by_period,
        'model_exists': os.path.exists(MODEL_FILE),
        'potential_model_exists': os.path.exists(POTENTIAL_MODEL_FILE),
        'gain_model_exists': os.path.exists(GAIN_MODEL_FILE),
    }
