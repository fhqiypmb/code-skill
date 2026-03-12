"""
影子学习器 - 自动存储所有能获取到的数据
"""

import pickle
import os
import time
from datetime import datetime
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
    影子学习器 - 自动存储所有能获取到的数据
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
        """保存数据"""
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
    
    def _flatten_dict(self, d: Dict, parent_key='', sep='_') -> Dict:
        """把嵌套的字典打平，变成单层字典"""
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)
    
    def _get_all_data(self, code: str, screener_details: Dict, analyzer_result: Dict) -> Dict:
        """
        获取所有能拿到的数据
        """
        record = {}
        
        # ===== 基础信息 =====
        record['code'] = code
        record['name'] = screener_details.get('name', '')
        record['date'] = datetime.now().strftime('%Y-%m-%d')
        record['timestamp'] = time.time()
        
        # ===== 1. 选股阶段的所有数据 =====
        # screener_details 里的所有字段都存
        for k, v in screener_details.items():
            if isinstance(v, (int, float, str, bool)):
                record[f'screener_{k}'] = v
        
        # ===== 2. 分析阶段的所有数据 =====
        # analyzer_result 是个嵌套字典，需要打平
        flat_analyzer = self._flatten_dict(analyzer_result)
        for k, v in flat_analyzer.items():
            if isinstance(v, (int, float, str, bool)):
                record[f'analyzer_{k}'] = v
        
        # ===== 3. 原始K线数据 =====
        try:
            klines = data_source.fetch_kline(code, period='240min', limit=30)
            if klines and len(klines) > 0:
                # 存最近一根K线的所有数据
                last_k = klines[-1]
                record['kline_close'] = float(last_k.get('close', 0))
                record['kline_open'] = float(last_k.get('open', 0))
                record['kline_high'] = float(last_k.get('high', 0))
                record['kline_low'] = float(last_k.get('low', 0))
                record['kline_volume'] = float(last_k.get('volume', 0))
                
                # 计算简单指标
                if len(klines) >= 5:
                    closes = [float(k['close']) for k in klines[-5:]]
                    record['kline_ma5'] = sum(closes) / 5
                if len(klines) >= 20:
                    closes = [float(k['close']) for k in klines[-20:]]
                    record['kline_ma20'] = sum(closes) / 20
        except Exception as e:
            print(f"  ⚠️ 获取K线数据失败: {e}")
        
        # ===== 4. 实时行情数据 =====
        try:
            quote = data_source.fetch_realtime_quote(code)
            if quote:
                for k, v in quote.items():
                    if isinstance(v, (int, float, str)):
                        record[f'quote_{k}'] = v
        except Exception as e:
            print(f"  ⚠️ 获取实时行情失败: {e}")
        
        # ===== 5. 资金流向数据 =====
        try:
            capital = data_source.fetch_capital_flow(code)
            if capital:
                for k, v in capital.items():
                    if isinstance(v, (int, float)):
                        record[f'capital_{k}'] = v
        except Exception as e:
            print(f"  ⚠️ 获取资金流向失败: {e}")
        
        # ===== 6. 行业概念数据 =====
        try:
            industry = data_source.fetch_stock_industry(code)
            if industry:
                record['industry'] = industry.get('industry', '')
                record['industry_board'] = industry.get('board_code', '')
        except:
            pass
        
        try:
            concepts = data_source.fetch_stock_concepts(code)
            if concepts:
                record['concepts'] = ','.join(concepts[:5])  # 只存前5个
        except:
            pass
        
        # ===== 7. 大盘数据 =====
        try:
            # 上证指数
            sh_index = data_source.fetch_index_kline('000001', days=5)
            if sh_index and len(sh_index) > 0:
                record['sh_index_close'] = sh_index[-1].get('close', 0)
                if len(sh_index) > 1:
                    record['sh_index_change'] = (sh_index[-1].get('close', 0) - sh_index[-2].get('close', 0)) / sh_index[-2].get('close', 0)
                else:
                    record['sh_index_change'] = 0
            
            # 深证成指
            sz_index = data_source.fetch_index_kline('399001', days=5)
            if sz_index and len(sz_index) > 0:
                record['sz_index_close'] = sz_index[-1].get('close', 0)
                if len(sz_index) > 1:
                    record['sz_index_change'] = (sz_index[-1].get('close', 0) - sz_index[-2].get('close', 0)) / sz_index[-2].get('close', 0)
                else:
                    record['sz_index_change'] = 0
            
            # 创业板指
            cy_index = data_source.fetch_index_kline('399006', days=5)
            if cy_index and len(cy_index) > 0:
                record['cy_index_close'] = cy_index[-1].get('close', 0)
        except:
            pass
        
        # ===== 8. 结果字段（先留空）=====
        record['reached_target'] = None
        record['actual_return'] = None
        record['updated'] = False
        
        return record
    
    def record_signal(self, code: str, name: str, screener_details: Dict, analyzer_result: Dict):
        """
        记录一次信号的所有数据
        """
        record = self._get_all_data(code, screener_details, analyzer_result)
        
        self.training_data.append(record)
        print(f"📝 已记录 {code} {name} [{screener_details.get('screener_signal_type', '普通')}] 信号")
        print(f"   共存储 {len(record)} 个字段")
        
        # 每次都保存
        self._save_data()
        
        return record
    
    def update_outcomes(self, days_later: int = 5):
        """
        更新实际结果
        """
        print(f"\n🔄 更新 {len(self.training_data)} 条记录的实际结果...")
        
        updated_count = 0
        for record in self.training_data:
            if record.get('updated', False):
                continue
            
            code = record['code']
            target_price = record.get('analyzer_technical_target_price', 0)
            record_price = record.get('kline_close', 0) or record.get('quote_price', 0)
            
            if record_price == 0 or target_price == 0:
                continue
            
            try:
                klines = data_source.fetch_kline(code, period='240min', limit=10)
                if len(klines) < 2:
                    continue
                
                current_price = float(klines[-1]['close'])
                actual_return = (current_price - record_price) / record_price
                reached = 1 if current_price >= target_price else 0
                
                record['actual_return'] = actual_return
                record['reached_target'] = reached
                record['updated'] = True
                record['update_date'] = datetime.now().strftime('%Y-%m-%d')
                record['current_price'] = current_price
                
                updated_count += 1
                print(f"  ✓ {code}: 目标{target_price:.2f}, 现{current_price:.2f}, "
                      f"{'✅' if reached else '❌'} ({actual_return*100:.1f}%)")
                
                time.sleep(1)
            except Exception as e:
                print(f"  ✗ {code} 更新失败: {e}")
        
        print(f"\n✅ 更新完成: {updated_count} 条")
        self._save_data()
        return updated_count
    
    def prepare_training_data(self):
        """准备训练数据"""
        labeled_data = [r for r in self.training_data if r.get('reached_target') is not None]
        
        if len(labeled_data) < 50:
            print(f"❌ 训练数据不足: {len(labeled_data)} < 50")
            return None, None, None
        
        # 找出所有数值型字段
        exclude_fields = ['code', 'name', 'date', 'update_date', 'concepts', 
                         'industry', 'industry_board', 'timestamp']
        
        feature_fields = []
        for field in labeled_data[0].keys():
            if field not in exclude_fields and isinstance(labeled_data[0][field], (int, float)):
                feature_fields.append(field)
        
        print(f"\n📊 准备训练数据: {len(labeled_data)} 条, {len(feature_fields)} 个特征")
        
        X = []
        y = []
        for record in labeled_data:
            features = [record.get(field, 0) for field in feature_fields]
            X.append(features)
            y.append(record['reached_target'])
        
        return np.array(X), np.array(y), feature_fields
    
    def train(self):
        """训练模型"""
        if not ML_AVAILABLE:
            print("❌ 请安装机器学习库")
            return None
        
        X, y, feature_fields = self.prepare_training_data()
        
        if X is None or len(X) < 50:
            return None
        
        print(f"\n🎯 开始训练，使用 {len(X)} 条已标记数据")
        print(f"   特征数量: {len(feature_fields)} 个")
        print(f"   正样本比例: {y.mean():.2%}")
        
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            min_samples_leaf=2,
            random_state=42,
            n_jobs=-1
        )
        
        self.model.fit(X_train, y_train)
        
        train_pred = self.model.predict(X_train)
        test_pred = self.model.predict(X_test)
        
        train_acc = accuracy_score(y_train, train_pred)
        test_acc = accuracy_score(y_test, test_pred)
        
        print(f"\n📊 模型评估:")
        print(f"  训练集准确率: {train_acc:.2%}")
        print(f"  测试集准确率: {test_acc:.2%}")
        
        # 特征重要性
        importance = list(zip(feature_fields, self.model.feature_importances_))
        importance.sort(key=lambda x: x[1], reverse=True)
        
        print(f"\n🎯 特征重要性排名（前20）:")
        print(f"{'='*70}")
        for i, (name, imp) in enumerate(importance[:20]):
            print(f"{i+1:3d}. {name:<40} {imp:.4f}")
        
        # 保存模型
        self.feature_names = feature_fields
        joblib.dump({
            'model': self.model,
            'feature_names': feature_fields,
            'importance': importance
        }, self.model_file)
        print(f"\n💾 模型已保存: {self.model_file}")
        
        return self.model
    
    def get_stats(self):
        """获取统计数据"""
        total = len(self.training_data)
        labeled = len([r for r in self.training_data if r.get('reached_target') is not None])
        
        if labeled > 0:
            accuracy = np.mean([r['reached_target'] for r in self.training_data 
                               if r.get('reached_target') is not None])
        else:
            accuracy = 0
        
        return {
            'total_records': total,
            'labeled_records': labeled,
            'overall_accuracy': accuracy,
            'model_trained': self.model is not None,
            'feature_count': len(self.training_data[0]) if self.training_data else 0
        }


# ========== 全局实例 ==========
_shadow = None

def get_shadow():
    """获取影子学习器单例"""
    global _shadow
    if _shadow is None:
        _shadow = ShadowLearner()
    return _shadow


def record_signal_from_screener(code: str, name: str, screener_details: Dict):
    """
    从严格选股_多周期.py 调用
    记录所有数据
    """
    shadow = get_shadow()
    
    try:
        result = analyze_stock(code, name, signal_type=screener_details.get('signal_type', ''))
        shadow.record_signal(code, name, screener_details, result)
        return result
    except Exception as e:
        print(f"❌ 基本面分析失败 {code}: {e}")
        return None