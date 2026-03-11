"""
影子学习器 - 机器学习学习你的选股系统
完全不改动你的选股和分析系统
"""

import pickle
import os
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple
import numpy as np

# 机器学习库
try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score
    import joblib
    ML_AVAILABLE = True
except ImportError:
    print("⚠️ 请安装机器学习库: pip install scikit-learn joblib")
    ML_AVAILABLE = False

# 导入你的系统和数据源
import data_source
from stock_analyzer import analyze_stock


class ShadowLearner:
    """
    影子学习器 - 默默学习你的选股系统
    
    工作流程：
    1. 你每天运行严格选股_多周期.py → 得到信号股票
    2. 影子学习器记录这些信号股票和你的分析结果
    3. 几天后看这些股票实际走势
    4. 机器学习学习你的系统什么时候准、什么时候不准
    """
    
    def __init__(self, data_file='shadow_data.pkl', model_file='shadow_model.pkl'):
        self.data_file = data_file
        self.model_file = model_file
        self.training_data = []
        self.model = None
        self.feature_names = None
        
        # 加载已有数据
        self._load_data()
        
        # 加载已有模型
        self._load_model()
    
    def _load_data(self):
        """加载历史数据"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'rb') as f:
                    self.training_data = pickle.load(f)
                print(f"📂 加载历史数据: {len(self.training_data)} 条记录")
            except:
                print("⚠️ 历史数据加载失败，重新开始")
                self.training_data = []
    
    def _save_data(self):
        """保存数据 - 每次都保存"""
        with open(self.data_file, 'wb') as f:
            pickle.dump(self.training_data, f)
        print(f"💾 数据已保存: {len(self.training_data)} 条记录")
    
    def _load_model(self):
        """加载训练好的模型"""
        if ML_AVAILABLE and os.path.exists(self.model_file):
            try:
                self.model = joblib.load(self.model_file)
                print(f"🤖 加载训练好的模型: {self.model_file}")
            except:
                print("⚠️ 模型加载失败")
    
    def _extract_features(self, analyzer_result: Dict) -> Dict:
        """
        从 stock_analyzer 的结果中提取特征
        这是机器学习要学习的输入
        """
        features = {}
        
        # 1. 成功率各维度（你的核心评分）
        sr = analyzer_result.get('success_rate', {})
        features['breakout_score'] = sr.get('dim_breakout', 0)      # 突破质量
        features['momentum_score'] = sr.get('dim_momentum', 0)      # 趋势动能
        features['rs_score'] = sr.get('dim_rs', 0)                  # 相对强度
        features['capital_score'] = sr.get('dim_capital', 0)        # 资金持续
        features['rr_score'] = sr.get('dim_rr', 0)                  # 风险收益
        features['reach_score'] = sr.get('dim_reach_prob', 0)       # 到达概率
        features['total_score'] = sr.get('score', 0)                # 总分
        
        # 2. 趋势维度
        trend = analyzer_result.get('trend', {})
        features['trend_score'] = trend.get('score', 50)
        features['macd_strength'] = trend.get('macd_strength', 50)
        features['ma_align'] = 1 if trend.get('ma_align') else 0
        features['vol_price_ok'] = 1 if trend.get('vol_price_ok') else 0
        
        # 3. 市场位置
        market = analyzer_result.get('market_pos', {})
        features['market_score'] = market.get('score', 50)
        features['relative_strength'] = market.get('relative_strength', 0)
        features['vol_ratio'] = market.get('vol_ratio', 1)
        
        # 4. 技术目标
        tech = analyzer_result.get('technical', {})
        features['expected_gain'] = tech.get('expected_gain_pct', 0)
        features['stop_loss'] = abs(tech.get('stop_loss_pct', 0))
        features['space_ok'] = 1 if tech.get('space_ok') else 0
        
        # 5. 资金数据
        capital = analyzer_result.get('capital', {})
        features['main_net_in'] = capital.get('main_net_in', 0)
        features['flow_ratio'] = capital.get('flow_ratio', 0)
        
        # 6. 最终 verdict
        features['verdict_dabiao'] = 1 if analyzer_result.get('verdict') == '达标' else 0
        
        return features
    
    def record_signal(self, code: str, name: str, signal_type: str, analyzer_result: Dict):
        """
        记录一次信号和分析结果
        在严格选股_多周期.py 得到信号后调用
        """
        # 提取特征
        features = self._extract_features(analyzer_result)
        
        # 创建记录
        record = {
            'code': code,
            'name': name,
            'signal_type': signal_type,  # '严格', '筑底', '突破', '普通'
            'timestamp': time.time(),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'price': analyzer_result.get('quote', {}).get('price', 0),
            'target': analyzer_result.get('technical', {}).get('target_price', 0),
            'your_score': features['total_score'],
            'features': features,
            'actual_return': None,      # 待填充
            'reached_target': None,      # 待填充
            'updated': False
        }
        
        self.training_data.append(record)
        print(f"📝 已记录 {code} {name} [{signal_type}] 信号")
        
        # ===== 每次都保存，确保数据不丢 =====
        self._save_data()
    
    def update_outcomes(self, days_later: int = 5):
        """
        更新实际结果
        几天后看股票实际走势，你的判断对不对
        """
        print(f"\n🔄 更新 {len(self.training_data)} 条记录的实际结果...")
        
        updated_count = 0
        for record in self.training_data:
            # 跳过已更新的
            if record.get('updated', False):
                continue
            
            code = record['code']
            record_price = record['price']
            target_price = record['target']
            
            try:
                # 获取当前K线（用日线）
                klines = data_source.fetch_kline(code, period='240min', limit=10)
                if len(klines) < 2:
                    continue
                
                # 当前价格
                current_price = float(klines[-1]['close'])
                
                # 计算实际涨幅
                actual_return = (current_price - record_price) / record_price
                
                # 是否达到目标价
                reached = 1 if current_price >= target_price else 0
                
                # 更新记录
                record['actual_return'] = actual_return
                record['reached_target'] = reached
                record['updated'] = True
                record['update_date'] = datetime.now().strftime('%Y-%m-%d')
                
                updated_count += 1
                print(f"  ✓ {code}: 目标{target_price:.2f}, 现{current_price:.2f}, "
                      f"{'✅达成' if reached else '❌未达成'} ({actual_return*100:.1f}%)")
                
                # 限流
                time.sleep(1)
                
            except Exception as e:
                print(f"  ✗ {code} 更新失败: {e}")
        
        print(f"\n✅ 更新完成: {updated_count} 条")
        self._save_data()
        return updated_count
    
    def train(self):
        """训练模型，学习你的系统什么时候准"""
        if not ML_AVAILABLE:
            print("❌ 请安装机器学习库")
            return None
        
        # 筛选有标签的数据
        labeled_data = [r for r in self.training_data if r.get('reached_target') is not None]
        
        if len(labeled_data) < 50:
            print(f"❌ 训练数据不足: {len(labeled_data)} < 50")
            print("请先收集更多数据，或用 update_outcomes() 更新标签")
            return None
        
        print(f"\n🎯 开始训练，使用 {len(labeled_data)} 条已标记数据")
        
        # 准备特征矩阵
        features_list = []
        labels = []
        
        for record in labeled_data:
            features = record['features']
            features_list.append(list(features.values()))
            labels.append(record['reached_target'])
        
        # 特征名称
        self.feature_names = list(labeled_data[0]['features'].keys())
        
        X = np.array(features_list)
        y = np.array(labels)
        
        print(f"正样本比例: {y.mean():.2%}")
        
        # 划分训练集和测试集
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        
        # 训练随机森林
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            min_samples_leaf=2,
            random_state=42,
            n_jobs=-1
        )
        
        self.model.fit(X_train, y_train)
        
        # 评估
        train_pred = self.model.predict(X_train)
        test_pred = self.model.predict(X_test)
        
        train_acc = accuracy_score(y_train, train_pred)
        test_acc = accuracy_score(y_test, test_pred)
        
        print(f"\n📊 模型评估:")
        print(f"  训练集准确率: {train_acc:.2%}")
        print(f"  测试集准确率: {test_acc:.2%}")
        
        # 特征重要性
        importance = zip(self.feature_names, self.model.feature_importances_)
        print("\n🎯 你的系统中最准的维度（特征重要性）:")
        for name, imp in sorted(importance, key=lambda x: x[1], reverse=True)[:10]:
            print(f"  {name}: {imp:.3f}")
        
        # 保存模型
        joblib.dump(self.model, self.model_file)
        print(f"\n💾 模型已保存: {self.model_file}")
        
        return self.model
    
    def predict_confidence(self, analyzer_result: Dict) -> Dict:
        """
        预测你的分析结果有多可靠
        
        返回:
        {
            'confidence': 0.85,  # 你的判断正确的概率
            'level': '高置信度',   # 高/中/低
            'advice': '建议重仓'   # 建议
        }
        """
        if self.model is None:
            return {
                'confidence': 0.5,
                'level': '未知',
                'advice': '模型未训练，请先收集数据'
            }
        
        # 提取特征
        features = self._extract_features(analyzer_result)
        X = np.array([list(features.values())])
        
        # 预测概率
        proba = self.model.predict_proba(X)[0]
        
        # 正类的概率（你的判断正确的概率）
        confidence = proba[1] if len(proba) > 1 else proba[0]
        
        # 置信度等级
        if confidence >= 0.7:
            level = '高置信度'
            advice = '✅ 机器学习高度认可你的判断'
        elif confidence >= 0.5:
            level = '中等置信度'
            advice = '👍 机器学习认可你的判断'
        elif confidence >= 0.3:
            level = '低置信度'
            advice = '⚠️ 机器学习对你的判断存疑'
        else:
            level = '极低置信度'
            advice = '❌ 机器学习不看好你的判断'
        
        return {
            'confidence': round(float(confidence), 3),
            'level': level,
            'advice': advice,
            'your_score': features['total_score']
        }
    
    def get_stats(self):
        """获取统计数据"""
        total = len(self.training_data)
        labeled = len([r for r in self.training_data if r.get('reached_target') is not None])
        
        if labeled > 0:
            accuracy = np.mean([r['reached_target'] for r in self.training_data if r.get('reached_target') is not None])
        else:
            accuracy = 0
        
        # 按信号类型统计
        signal_stats = {}
        for record in self.training_data:
            st = record.get('signal_type', '普通')
            if st not in signal_stats:
                signal_stats[st] = {'total': 0, 'correct': 0}
            signal_stats[st]['total'] += 1
            if record.get('reached_target') == 1:
                signal_stats[st]['correct'] += 1
        
        return {
            'total_records': total,
            'labeled_records': labeled,
            'overall_accuracy': accuracy,
            'signal_stats': signal_stats,
            'model_trained': self.model is not None
        }


# ========== 全局实例 ==========
_shadow = None

def get_shadow():
    """获取影子学习器单例"""
    global _shadow
    if _shadow is None:
        _shadow = ShadowLearner()
    return _shadow


def record_signal_from_screener(code: str, name: str, signal_type: str):
    """
    从严格选股_多周期.py 调用
    记录信号并获取基本面分析
    """
    shadow = get_shadow()
    
    # 调用你的基本面分析
    try:
        result = analyze_stock(code, name, signal_type=signal_type)
        
        # 记录
        shadow.record_signal(code, name, signal_type, result)
        
        # 返回ML置信度（如果有模型）
        ml_result = shadow.predict_confidence(result)
        
        return result, ml_result
    except Exception as e:
        print(f"❌ 基本面分析失败 {code}: {e}")
        return None, None