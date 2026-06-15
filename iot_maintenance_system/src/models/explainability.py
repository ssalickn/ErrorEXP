"""
SHAP-based Explainability Module
Provides interpretable explanations for model predictions
"""

import pandas as pd
import numpy as np
import shap
import joblib
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)


class ExplainabilityEngine:
    """
    Generate SHAP explanations for IoT device failure predictions
    """
    
    def __init__(self, model_paths: Dict[str, str]):
        """
        Initialize explainability engine with trained models
        
        Args:
            model_paths: Dict with paths to scaler, xgb_model, feature_names
        """
        self.scaler = joblib.load(model_paths['scaler'])
        self.classification_model = joblib.load(model_paths['classification'])
        self.feature_names = joblib.load(model_paths['feature_names'])
        
        # Create SHAP explainer
        self.shap_explainer = shap.TreeExplainer(self.classification_model)
        
        logger.info("ExplainabilityEngine initialized")
    
    def explain_single_prediction(
        self,
        device_features: np.ndarray,
        device_id: str,
        failure_mode: str = None
    ) -> Dict:
        """
        Generate SHAP explanation for a single device prediction
        
        Args:
            device_features: Feature vector (unscaled, matching feature_names length)
            device_id: Device identifier
            failure_mode: (optional) Predicted failure mode
            
        Returns:
            Dict with SHAP values, base value, and interpretation
        """
        # Ensure correct array shape and convert safely to float
        features_clean = np.asarray(device_features, dtype=np.float64).reshape(1, -1)
        
        # Scale features
        X_scaled = self.scaler.transform(features_clean)
        
        # Get predictions
        prediction_proba = self.classification_model.predict_proba(X_scaled)[0]
        predicted_class = np.argmax(prediction_proba)
        
        # Get class name
        failure_classes = self.classification_model.classes_
        predicted_failure_mode = failure_classes[predicted_class]
        confidence = prediction_proba[predicted_class]
        
        # Compute SHAP values for this prediction
        shap_values = self.shap_explainer.shap_values(X_scaled)
        
        # Handle shape differences between binary/multiclass or older/newer SHAP versions safely
        if isinstance(shap_values, list):
            device_shap = shap_values[predicted_class][0]
            base_value = self.shap_explainer.expected_value[predicted_class]
        elif len(shap_values.shape) == 3:  # Shape: [samples, features, classes] or [classes, samples, features]
            if shap_values.shape[0] == len(failure_classes):
                device_shap = shap_values[predicted_class][0]
                base_value = self.shap_explainer.expected_value[predicted_class]
            else:
                device_shap = shap_values[0, :, predicted_class]
                base_value = self.shap_explainer.expected_value[predicted_class]
        else:
            device_shap = shap_values[0]
            base_value = self.shap_explainer.expected_value
            
        # Create explanation dict
        explanation = {
            'device_id': device_id,
            'predicted_failure_mode': str(predicted_failure_mode),
            'confidence': float(confidence),
            'base_value': float(base_value if np.isscalar(base_value) else base_value[0]),
            'all_probabilities': {
                str(failure_classes[i]): float(prediction_proba[i])
                for i in range(len(failure_classes))
            },
            'feature_contributions': self._format_shap_values(
                device_shap,
                features_clean[0],
                base_value
            ),
            'feature_importances': self._rank_features(
                device_shap,
                self.feature_names
            )
        }
        
        return explanation
    
    def _format_shap_values(
        self,
        shap_values: np.ndarray,
        feature_values: np.ndarray,
        base_value: float
    ) -> List[Dict]:
        """
        Format SHAP values for human interpretation
        """
        formatted = []
        
        for i, (feat_name, shap_val, feat_val) in enumerate(
            zip(self.feature_names, shap_values, feature_values)
        ):
            contribution = {
                'feature': feat_name,
                'value': float(feat_val),
                'shap_value': float(shap_val),
                'impact_direction': 'increases_risk' if shap_val > 0 else 'decreases_risk',
                'impact_magnitude': abs(float(shap_val)),
                'interpretation': self._interpret_feature(feat_name, feat_val, shap_val)
            }
            formatted.append(contribution)
        
        # Sort by absolute SHAP value
        formatted.sort(key=lambda x: x['impact_magnitude'], reverse=True)
        
        return formatted[:15]
    
    def _rank_features(self, shap_values: np.ndarray, feature_names: List[str]) -> Dict:
        """Rank features by absolute SHAP contribution"""
        rankings = {}
        for feat_name, shap_val in zip(feature_names, shap_values):
            rankings[feat_name] = float(np.abs(shap_val))
        return dict(sorted(rankings.items(), key=lambda x: x[1], reverse=True))
    
    def _interpret_feature(self, feature_name: str, value: float, shap_val: float) -> str:
        """
        Generate human-readable interpretation of a feature's contribution
        """
        interpretations = {
            'temp_persistence': {
                'threshold': 0.5,
                'high_interpretation': f"Device spent {value*100:.0f}% of time overheated (>75°C); cooling failure?"
            },
            'voltage_volatility': {
                'threshold': 1.0,
                'high_interpretation': f"Power supply fluctuating ±{value:.1f}V; PSU degrading?"
            },
            'packet_loss_trend': {
                'threshold': 0.01,
                'high_interpretation': f"Packet loss increasing {value:.3f}%/day; interface degrading?"
            },
            'memory_pressure': {
                'threshold': 0.8,
                'high_interpretation': f"Device using {value*100:.0f}% of memory; software memory leak?"
            },
            'device_age_days': {
                'threshold': 1460,
                'high_interpretation': f"Device is {value:.0f} days old ({value/365:.1f} years); approaching end-of-life"
            },
            'days_since_last_failure': {
                'threshold': 0,
                'high_interpretation': f"Device failed {value:.0f} days ago; risk of recurring failures"
            },
        }
        
        if feature_name not in interpretations:
            return f"{feature_name} = {value:.2f}"
        
        spec = interpretations[feature_name]
        if abs(shap_val) > 0.01:
            if shap_val > 0:
                return spec['high_interpretation']
            else:
                return f"Low {feature_name}: {value:.2f} (protective factor)"
        
        return f"{feature_name}: {value:.2f}"
    
    def generate_alert_explanation(
        self,
        device_features: np.ndarray,
        device_id: str,
        device_type: str,
        zone: str,
        ttf_estimate: float,
        failure_prob_7d: float
    ) -> Dict:
        """
        Generate complete technician-facing explanation
        """
        shap_explanation = self.explain_single_prediction(device_features, device_id)
        failure_mode = shap_explanation['predicted_failure_mode']
        
        recommended_actions = self._get_actions(
            failure_mode, device_type, zone, ttf_estimate, failure_prob_7d,
            shap_explanation['feature_contributions']
        )
        
        alert = {
            'device_id': device_id,
            'device_type': device_type,
            'zone': zone,
            'predicted_failure_mode': failure_mode,
            'confidence': shap_explanation['confidence'],
            'risk_level': self._classify_risk(failure_prob_7d),
            'failure_probability_7d': float(failure_prob_7d),
            'estimated_ttf_days': float(ttf_estimate),
            'top_contributing_factors': shap_explanation['feature_contributions'][:5],
            'recommended_actions': recommended_actions,
            'all_failure_probabilities': shap_explanation['all_probabilities'],
            'confidence_metrics': {
                'model_confidence': float(shap_explanation['confidence']),
                'feature_count': len(shap_explanation['feature_importances']),
                'top_feature_importance': max(shap_explanation['feature_importances'].values()) if shap_explanation['feature_importances'] else 0
            }
        }
        
        return alert
    
    def _classify_risk(self, failure_prob_7d: float) -> str:
        if failure_prob_7d > 0.75: return "CRITICAL"
        elif failure_prob_7d > 0.50: return "HIGH"
        elif failure_prob_7d > 0.30: return "MEDIUM"
        else: return "LOW"
    
    def _get_actions(
        self, failure_mode: str, device_type: str, zone: str, ttf: float, failure_prob: float, shap_features: List[Dict]
    ) -> List[Dict]:
        actions = []
        if failure_prob > 0.75:
            actions.append({
                'priority': 1, 'action': 'Schedule device replacement', 'timeline': 'Within 5 days',
                'rationale': f'High failure probability ({failure_prob*100:.0f}%) with imminent timeline ({ttf:.1f} days)'
            })
        elif failure_prob > 0.50:
            actions.append({
                'priority': 1, 'action': 'Schedule device replacement', 'timeline': 'Within 14 days',
                'rationale': f'Elevated failure probability ({failure_prob*100:.0f}%) detected'
            })
            
        if failure_mode == 'Hardware_Burnout':
            actions.append({'priority': 2, 'action': f'Investigate cooling in {zone}', 'timeline': 'Within 24 hours', 'rationale': 'Thermal stress detected'})
        elif failure_mode == 'Firmware_Crash':
            actions.append({'priority': 2, 'action': 'Plan firmware update/rollback', 'timeline': 'Before replacement', 'rationale': 'Memory/CPU stress indications'})
        elif failure_mode == 'Network_Interface':
            actions.append({'priority': 2, 'action': 'Inspect network cables/ports', 'timeline': 'Within 24 hours', 'rationale': 'Interface degradation issues'})
        elif failure_mode == 'Power_Supply':
            actions.append({'priority': 2, 'action': f'Check power supply in {zone}', 'timeline': 'Within 12 hours', 'rationale': 'Voltage instability detected'})
            
        return actions


def main():
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    model_paths = {
        'scaler': 'models/scaler_v1.pkl',
        'classification': 'models/xgboost_rca_v1.pkl',
        'feature_names': 'models/feature_names_v1.pkl'
    }
    
    engine = ExplainabilityEngine(model_paths)
    
    # Load features and dynamically drop non-numeric structural columns
    features_df = pd.read_parquet('data/processed_features.parquet')
    
    exclude_cols = ['device_id', 'device_type', 'zone', 'install_date', 
                    'ttf_days', 'failure_observed', 'failure_mode']
    
    # Isolate exact numeric feature matrix columns matching model training definitions
    feature_cols = [c for c in features_df.columns if c not in exclude_cols]
    sample_features = features_df[feature_cols].iloc[0].values
    
    # Generate explanation
    explanation = engine.explain_single_prediction(
        sample_features,
        features_df.iloc[0]['device_id']
    )
    
    print("\n=== SHAP Explanation Sample ===")
    print(f"Device: {explanation['device_id']}")
    print(f"Predicted Mode: {explanation['predicted_failure_mode']}")
    print(f"Confidence: {explanation['confidence']:.2%}")
    print(f"\nTop Contributing Factors:")
    for feat in explanation['feature_contributions'][:5]:
        print(f"  - {feat['feature']}: {feat['impact_direction']} ({feat['impact_magnitude']:.4f})")


if __name__ == "__main__":
    main()