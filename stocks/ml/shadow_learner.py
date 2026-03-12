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

# 多少天后回填实际结果
OUTCOME_DAYS = 5

# 训练所需的最少已标记样本数
MIN_TRAIN_SAMPLES = 50


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

        if not entry_price or not target_price:
            continue

        try:
            klines = data_source.fetch_kline(code, period='240min', limit=10)
            if not klines:
                continue

            exit_price = float(klines[-1].get('close', 0))
            if not exit_price:
                continue

            actual_return = (exit_price - entry_price) / entry_price
            reached       = 1 if exit_price >= target_price else 0

            record['reached_target'] = reached
            record['actual_return']  = round(actual_return, 4)
            record['exit_price']     = exit_price
            record['exit_date']      = datetime.now().strftime('%Y-%m-%d')
            updated += 1

            logger.info(
                f"回填 {code}: 入{entry_price:.2f} 目标{target_price:.2f} "
                f"现{exit_price:.2f} {'达标' if reached else '未达标'} ({actual_return*100:.1f}%)"
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
    """训练随机森林模型，返回模型对象，失败返回 None"""
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
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

    # 排除元信息和结果字段，只保留数值型特征
    exclude = {
        'date', 'code', 'name', 'period', 'signal_type', 'timestamp',
        'gold_cross_date', 'confirm_date', 'verdict', 'industry',
        'sr_grade', 'exit_date', 'reached_target', 'actual_return', 'exit_price',
    }
    feature_fields = sorted({
        k for r in labeled for k, v in r.items()
        if k not in exclude and isinstance(v, (int, float))
    })

    logger.info(f"训练样本: {len(labeled)} 条，特征: {len(feature_fields)} 个")

    X = np.array([[r.get(f, 0) or 0 for f in feature_fields] for r in labeled], dtype=float)
    y = np.array([r['reached_target'] for r in labeled], dtype=int)

    logger.info(f"正样本比例(达标): {y.mean():.2%}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42,
        stratify=y if len(set(y)) > 1 else None
    )

    model = RandomForestClassifier(
        n_estimators=100, max_depth=10,
        min_samples_split=5, min_samples_leaf=2,
        class_weight='balanced', random_state=42, n_jobs=-1,
    )
    model.fit(X_train, y_train)

    train_acc = accuracy_score(y_train, model.predict(X_train))
    test_acc  = accuracy_score(y_test,  model.predict(X_test))
    logger.info(f"训练集准确率: {train_acc:.2%}  测试集准确率: {test_acc:.2%}")
    logger.info(f"\n{classification_report(y_test, model.predict(X_test))}")

    importance = sorted(
        zip(feature_fields, model.feature_importances_),
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
        'train_date':    datetime.now().strftime('%Y-%m-%d'),
        'sample_count':  len(labeled),
    }
    joblib.dump(bundle, MODEL_FILE)
    logger.info(f"模型已保存: {MODEL_FILE}")

    _save_report(bundle, labeled, y, feature_fields)
    return model


def _save_report(bundle: Dict, labeled: List[Dict], y, feature_fields: List[str]) -> None:
    """生成模型分析报告 model_report.md"""
    report_file = os.path.join(_ML_DIR, 'model_report.md')
    train_date  = bundle['train_date']
    train_acc   = bundle['train_acc']
    test_acc    = bundle['test_acc']
    sample_count = bundle['sample_count']
    importance  = bundle['importance']

    lines = []
    lines.append(f"# ML模型分析报告")
    lines.append(f"\n> 训练日期: {train_date}  |  样本数: {sample_count}  |  训练集准确率: {train_acc:.2%}  |  测试集准确率: {test_acc:.2%}")

    # ── 按周期达标率 ──
    lines.append(f"\n## 按周期达标率")
    lines.append("| 周期 | 总信号 | 达标数 | 达标率 |")
    lines.append("|------|--------|--------|--------|")
    from collections import defaultdict
    period_stats = defaultdict(lambda: {'total': 0, 'hit': 0})
    for r in labeled:
        p = r.get('period', '?')
        period_stats[p]['total'] += 1
        period_stats[p]['hit']   += r.get('reached_target', 0)
    for p, s in sorted(period_stats.items()):
        rate = s['hit'] / s['total'] if s['total'] else 0
        lines.append(f"| {p} | {s['total']} | {s['hit']} | {rate:.1%} |")

    # ── 按信号类型达标率 ──
    lines.append(f"\n## 按信号类型达标率")
    lines.append("| 信号类型 | 总信号 | 达标数 | 达标率 |")
    lines.append("|----------|--------|--------|--------|")
    type_stats = defaultdict(lambda: {'total': 0, 'hit': 0})
    for r in labeled:
        t = r.get('signal_type', '?')
        type_stats[t]['total'] += 1
        type_stats[t]['hit']   += r.get('reached_target', 0)
    for t, s in sorted(type_stats.items()):
        rate = s['hit'] / s['total'] if s['total'] else 0
        lines.append(f"| {t} | {s['total']} | {s['hit']} | {rate:.1%} |")

    # ── 特征重要性 TOP20（容易上涨的信号特征） ──
    lines.append(f"\n## 特征重要性 TOP20")
    lines.append("越靠前的特征对模型预测影响越大，可理解为「决定上涨概率的关键因子」。")
    lines.append("")
    lines.append("| 排名 | 特征名 | 重要性得分 |")
    lines.append("|------|--------|------------|")
    for i, (fname, imp) in enumerate(importance[:20], 1):
        lines.append(f"| {i} | `{fname}` | {imp:.4f} |")

    # ── 高概率 vs 低概率信号特征均值对比 ──
    lines.append(f"\n## 高达标 vs 低达标信号特征对比")
    lines.append("对比达标(1)和未达标(0)样本的特征均值，差异大的特征是区分好坏信号的关键。")
    lines.append("")
    import numpy as np
    hit_records  = [r for r in labeled if r.get('reached_target') == 1]
    miss_records = [r for r in labeled if r.get('reached_target') == 0]

    # 只取 importance TOP15 的特征做对比
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

    # ── 结论 ──
    lines.append(f"\n## 结论摘要")
    top3 = [f for f, _ in importance[:3]]
    lines.append(f"- 最关键的3个特征: `{'` / `'.join(top3)}`")
    overall_rate = sum(r.get('reached_target', 0) for r in labeled) / len(labeled)
    lines.append(f"- 整体达标率: {overall_rate:.1%}（基准线，ML预测高于此值才有参考意义）")
    lines.append(f"- 测试集准确率 {test_acc:.2%}，{'模型有效' if test_acc > overall_rate + 0.05 else '模型效果有限，继续积累样本'}")

    with open(report_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    logger.info(f"模型报告已保存: {report_file}")


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
    }
