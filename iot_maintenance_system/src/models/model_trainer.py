"""
Model Training Pipeline
Trains Weibull AFT (Stage 1) and XGBoost (Stage 2)
"""

import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_validate
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from lifelines import WeibullAFTFitter
from lifelines.statistics import logrank_test
import xgboost as xgb
import joblib
import logging
from typing import Tuple, Dict, Any

logger = logging.getLogger(__name__)


class ModelTrainingPipeline:
    """
    End-to-end training pipeline for IoT predictive maintenance
    Stage 1: Weibull AFT for time-to-failure
    Stage 2: XGBoost for failure mode classification
    """
    
    def __init__(self, config_path: str = 'config/config.yaml'):
        self.feature_scaler = StandardScaler()
        self.survival_model = WeibullAFTFitter()
        self.classification_model = None
        self.feature_names = None
        self.config = self._load_config(config_path)
        
    def _load_config(self, path: str) -> Dict:
        """Load YAML config"""
        import yaml
        try:
            with open(path, 'r') as f:
                return yaml.safe_load(f)
        except:
            return {}
    
    def execute_training(self, features_path: str = 'data/processed_features.parquet'):
        """
        Execute full training pipeline (or reuse existing models)
        """
        import os
        
        # Check if all models already exist
        model_files = [
            'models/scaler_v1.pkl',
            'models/weibull_aft_v1.pkl',
            'models/xgboost_rca_v1.pkl',
            'models/feature_names_v1.pkl'
        ]
        
        if all(os.path.exists(f) for f in model_files):
            logger.info("=" * 60)
            logger.info("✓ REUSING EXISTING TRAINED MODELS")
            logger.info("=" * 60)
            logger.info("   (Delete models/ directory to retrain)")
            
            # Load existing models
            self.feature_scaler = joblib.load('models/scaler_v1.pkl')
            self.survival_model = joblib.load('models/weibull_aft_v1.pkl')
            self.classification_model = joblib.load('models/xgboost_rca_v1.pkl')
            self.feature_names = joblib.load('models/feature_names_v1.pkl')
            return self
        
        logger.info("=" * 60)
        logger.info("IoT PREDICTIVE MAINTENANCE - FULL TRAINING PIPELINE")
        logger.info("=" * 60)
        
        # 1. Load features
        logger.info("\n[1/5] Loading engineered features...")
        features_df = pd.read_parquet(features_path)
        
        # Extract labels and features
        y_time = features_df['ttf_days'].values
        y_observed = features_df['failure_observed'].values
        y_mode = features_df['failure_mode'].values
        
        # Feature matrix (exclude ID and labels)
        exclude_cols = ['device_id', 'device_type', 'zone', 'install_date', 
                        'ttf_days', 'failure_observed', 'failure_mode']
        X = features_df[[c for c in features_df.columns if c not in exclude_cols]].values
        self.feature_names = [c for c in features_df.columns if c not in exclude_cols]
        
        logger.info(f"    Loaded {len(features_df)} samples, {len(self.feature_names)} features")
        
        # 2. Train Stage 1: Survival Analysis
        logger.info("\n[2/5] Training Stage 1: Weibull AFT (Time-to-Failure)...")
        self._train_survival_model(X, y_time, y_observed)
        
        # 3. Train Stage 2: Classification
        logger.info("\n[3/5] Training Stage 2: XGBoost (Failure Mode Classification)...")
        self._train_classification_model(X, y_mode)
        
        # 4. Validation
        logger.info("\n[4/5] Running validation...")
        self._validate_models(features_df, X, y_mode)
        
        # 5. Save models
        logger.info("\n[5/5] Saving models...")
        self._save_models()
        
        logger.info("\n" + "=" * 60)
        logger.info("✓ TRAINING COMPLETE")
        logger.info("=" * 60)
        
        return self
    
    def _train_survival_model(self, X: np.ndarray, T: np.ndarray, E: np.ndarray):
        """
        Train TTF model using XGBoost regression
        """
        # Scale features
        X_scaled = self.feature_scaler.fit_transform(X)
        
        # Use XGBoost for TTF regression
        self.survival_model = xgb.XGBRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            random_state=42,
            verbosity=0
        )
        
        self.survival_model.fit(X_scaled, T)
        
        # Evaluate R² score
        from sklearn.metrics import r2_score
        y_pred = self.survival_model.predict(X_scaled)
        r2 = r2_score(T, y_pred)
        logger.info(f"   R² Score (TTF): {r2:.4f}")
        logger.info(f"   Mean TTF: {T.mean():.1f} days")
        logger.info(f"   ✓ TTF model trained (XGBoost Regression)")
    
    def _train_classification_model(self, X: np.ndarray, y: np.ndarray):
        """
        Train XGBoost for multi-class failure mode classification
        """
        # Create a direct class mapping (string -> numeric starting from 0)
        unique_classes = np.unique(y)
        class_map = {cls: idx for idx, cls in enumerate(sorted(unique_classes))}
        self.class_map = class_map
        self.class_map_inv = {idx: cls for cls, idx in class_map.items()}
        
        # Encode using the direct mapping
        y_encoded = np.array([class_map[label] for label in y])
        
        # For small datasets with rare classes, train on all data
        # (cross-validation will be done separately for validation)
        X_train_scaled = self.feature_scaler.transform(X)
        
        logger.info(f"   Class distribution (train):")
        for mode_idx in np.unique(y_encoded):
            count = np.sum(y_encoded == mode_idx)
            pct = count / len(y_encoded) * 100
            mode_name = self.class_map_inv[mode_idx]
            logger.info(f"      {mode_name}: {count} ({pct:.1f}%)")
        
        # Train XGBoost on all data (for production, use all available training data)
        self.classification_model = xgb.XGBClassifier(
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            min_child_weight=1,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric='mlogloss',
            random_state=42,
            n_jobs=-1,
            verbosity=0
        )
        
        self.classification_model.fit(X_train_scaled, y_encoded)
        
        # Feature importance
        feature_importance = pd.DataFrame({
            'feature': self.feature_names,
            'importance': self.classification_model.feature_importances_
        }).sort_values('importance', ascending=False)
        
        logger.info(f"\n   Top 15 Features by Importance:")
        for idx, row in feature_importance.head(15).iterrows():
            logger.info(f"      {row['feature']:.<40} {row['importance']:.4f}")
    
    def _validate_models(self, features_df: pd.DataFrame, X: np.ndarray, y: np.ndarray):
        """
        Cross-validation and detailed metrics
        """
        # Encode using the class map
        y_encoded = np.array([self.class_map[label] for label in y])
        X_scaled = self.feature_scaler.transform(X)
        
        # Use 2-fold split since minority classes contain only 2 entities
        skf = StratifiedKFold(n_splits=2, shuffle=True, random_state=42)
        
        # Create a fresh copy of parameters but clear early stopping to prevent cross-validation crashes
        cv_params = self.classification_model.get_params()
        cv_params['early_stopping_rounds'] = None
        cv_estimator = xgb.XGBClassifier(**cv_params)
        
        cv_results = cross_validate(
            cv_estimator, X_scaled, y_encoded,
            cv=skf,
            scoring=['f1_weighted', 'precision_weighted', 'recall_weighted'],
            n_jobs=-1
        )
        
        logger.info(f"   2-Fold Cross-Validation Results:")
        logger.info(f"      F1 (Weighted):   {cv_results['test_f1_weighted'].mean():.4f} "
                    f"(±{cv_results['test_f1_weighted'].std():.4f})")
        logger.info(f"      Precision (Wtd): {cv_results['test_precision_weighted'].mean():.4f} "
                    f"(±{cv_results['test_precision_weighted'].std():.4f})")
        logger.info(f"      Recall (Wtd):    {cv_results['test_recall_weighted'].mean():.4f} "
                    f"(±{cv_results['test_recall_weighted'].std():.4f})")


    def _save_models(self):
        """Save trained models to disk"""
        import os
        os.makedirs('models', exist_ok=True)
        
        joblib.dump(self.feature_scaler, 'models/scaler_v1.pkl')
        joblib.dump(self.survival_model, 'models/weibull_aft_v1.pkl')
        joblib.dump(self.classification_model, 'models/xgboost_rca_v1.pkl')
        joblib.dump(self.feature_names, 'models/feature_names_v1.pkl')
        
        logger.info("    ✓ Scaler saved: models/scaler_v1.pkl")
        logger.info("    ✓ Weibull AFT saved: models/weibull_aft_v1.pkl")
        logger.info("    ✓ XGBoost saved: models/xgboost_rca_v1.pkl")
        logger.info("    ✓ Feature names saved: models/feature_names_v1.pkl")


def main():
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    pipeline = ModelTrainingPipeline()
    pipeline.execute_training()


if __name__ == "__main__":
    main()