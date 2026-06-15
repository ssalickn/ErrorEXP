"""
Utility functions for IoT predictive maintenance system
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)


def calculate_device_risk_score(features: Dict[str, float]) -> Tuple[float, str]:
    """
    Calculate composite risk score for a device (0-1 scale)
    
    Args:
        features: Device feature dictionary
        
    Returns:
        (risk_score, risk_level)
    """
    
    weights = {
        'temp_persistence': 0.25,
        'voltage_volatility': 0.15,
        'packet_loss_mean_24h': 0.15,
        'memory_pressure': 0.20,
        'device_age_log': 0.15,
        'thermal_shock_events': 0.10,
    }
    
    risk_score = 0.0
    
    for feature, weight in weights.items():
        if feature in features:
            value = features[feature]
            
            # Normalize features to 0-1 scale
            if feature == 'temp_persistence':
                normalized = min(value, 1.0)
            elif feature == 'voltage_volatility':
                normalized = min(value / 3.0, 1.0)
            elif feature == 'packet_loss_mean_24h':
                normalized = min(value / 10.0, 1.0)
            elif feature == 'memory_pressure':
                normalized = min(value, 1.0)
            elif feature == 'device_age_log':
                normalized = min(value / 8.0, 1.0)
            elif feature == 'thermal_shock_events':
                normalized = min(value / 50.0, 1.0)
            else:
                normalized = 0.0
            
            risk_score += normalized * weight
    
    # Classify risk level
    if risk_score > 0.70:
        risk_level = 'CRITICAL'
    elif risk_score > 0.50:
        risk_level = 'HIGH'
    elif risk_score > 0.30:
        risk_level = 'MEDIUM'
    else:
        risk_level = 'LOW'
    
    return risk_score, risk_level


def format_prediction_output(prediction: Dict) -> Dict:
    """
    Format prediction output for API/Dashboard display
    
    Args:
        prediction: Raw prediction dictionary
        
    Returns:
        Formatted prediction with human-readable explanations
    """
    
    formatted = {
        'device_id': prediction.get('device_id'),
        'device_type': prediction.get('device_type'),
        'zone': prediction.get('zone'),
        'timestamp': datetime.utcnow().isoformat(),
        
        'failure_assessment': {
            'predicted_mode': prediction.get('predicted_failure_mode'),
            'confidence': f"{prediction.get('confidence', 0)*100:.1f}%",
            'risk_level': prediction.get('risk_level'),
            'probability_7d': f"{prediction.get('failure_probability_7d', 0)*100:.1f}%",
            'estimated_ttf_days': round(prediction.get('estimated_ttf_days', 0), 1),
        },
        
        'contributing_factors': [
            {
                'factor': f['feature'],
                'current_value': f['value'],
                'impact': 'Increases Risk' if f['shap_value'] > 0 else 'Decreases Risk',
                'explanation': f['interpretation']
            }
            for f in prediction.get('top_contributing_factors', [])
        ],
        
        'recommended_actions': prediction.get('recommended_actions', [])
    }
    
    return formatted


def generate_alert_message(prediction: Dict) -> str:
    """
    Generate human-readable alert message
    
    Args:
        prediction: Prediction dictionary
        
    Returns:
        Formatted alert message
    """
    
    msg = f"""
╔════════════════════════════════════════════════════════════╗
║                    ⚠️  DEVICE ALERT                        ║
╚════════════════════════════════════════════════════════════╝

Device: {prediction['device_id']} ({prediction['device_type']})
Zone: {prediction['zone']}
Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}

RISK ASSESSMENT:
  Risk Level: {prediction['risk_level']}
  Predicted Failure: {prediction['predicted_failure_mode']}
  Confidence: {prediction['confidence']:.1%}
  7-Day Failure Probability: {prediction['failure_probability_7d']:.1%}
  Estimated Time to Failure: {prediction['estimated_ttf_days']:.1f} days

TOP CONTRIBUTING FACTORS:
"""
    
    for i, factor in enumerate(prediction.get('top_contributing_factors', [])[:5], 1):
        msg += f"\n  {i}. {factor['feature']}\n"
        msg += f"     Current Value: {factor['value']:.3f}\n"
        msg += f"     Impact: {factor['interpretation']}\n"
    
    msg += "\nRECOMMENDED ACTIONS:\n"
    for action in prediction.get('recommended_actions', []):
        msg += f"\n  [{action['priority']}] {action['action']}\n"
        msg += f"      Timeline: {action['timeline']}\n"
        msg += f"      Rationale: {action['rationale']}\n"
    
    msg += "\n" + "="*60 + "\n"
    
    return msg


def calculate_asset_criticality(device_type: str, zone: str) -> str:
    """
    Determine device criticality for alert prioritization
    
    Args:
        device_type: Type of device
        zone: Installation zone
        
    Returns:
        Criticality level: CRITICAL, HIGH, MEDIUM, LOW
    """
    
    # Network switches are critical infrastructure
    if device_type == "Switch":
        return "CRITICAL"
    
    # Cameras in certain zones (e.g., entrance security)
    if device_type == "Camera" and zone in ["Zone-A", "Zone-B"]:
        return "HIGH"
    
    # Sensors are generally lower priority
    if device_type == "Sensor":
        return "MEDIUM"
    
    return "LOW"


def filter_devices_by_risk(features_df: pd.DataFrame, min_risk: float = 0.5) -> pd.DataFrame:
    """
    Filter devices above risk threshold
    
    Args:
        features_df: Features dataframe
        min_risk: Minimum risk score (0-1)
        
    Returns:
        Filtered dataframe with risk > min_risk
    """
    
    risk_scores = []
    for _, row in features_df.iterrows():
        risk_score, _ = calculate_device_risk_score(row.to_dict())
        risk_scores.append(risk_score)
    
    features_df['risk_score'] = risk_scores
    return features_df[features_df['risk_score'] >= min_risk].sort_values(
        'risk_score', ascending=False
    )


def estimate_maintenance_cost(failure_mode: str, device_type: str) -> Dict[str, float]:
    """
    Estimate cost of device failure
    
    Args:
        failure_mode: Type of failure
        device_type: Type of device
        
    Returns:
        Cost estimate dictionary
    """
    
    # Base replacement costs
    device_costs = {
        'Switch': 5000,
        'Camera': 2000,
        'Sensor': 500
    }
    
    # Downtime multipliers
    downtime_multipliers = {
        'Hardware_Burnout': 2.0,
        'Firmware_Crash': 1.5,
        'Network_Interface': 3.0,
        'Power_Supply': 2.5,
        'Environmental': 1.0
    }
    
    replacement_cost = device_costs.get(device_type, 1000)
    downtime_cost = replacement_cost * downtime_multipliers.get(failure_mode, 1.0)
    
    return {
        'replacement_cost': replacement_cost,
        'estimated_downtime_hours': [4, 6, 8, 12, 2][
            list(downtime_multipliers.keys()).index(failure_mode)
        ] if failure_mode in downtime_multipliers else 4,
        'downtime_cost': downtime_cost,
        'total_estimated_cost': replacement_cost + downtime_cost
    }


def main():
    """Test utility functions"""
    
    # Sample feature dictionary
    sample_features = {
        'device_id': 'DEV-0001',
        'device_type': 'Switch',
        'zone': 'Zone-A',
        'temp_persistence': 0.6,
        'voltage_volatility': 2.1,
        'packet_loss_mean_24h': 0.5,
        'memory_pressure': 0.75,
        'device_age_log': 7.5,
        'thermal_shock_events': 25,
    }
    
    # Calculate risk
    risk_score, risk_level = calculate_device_risk_score(sample_features)
    print(f"Risk Score: {risk_score:.3f} → {risk_level}")
    
    # Cost estimate
    cost = estimate_maintenance_cost('Hardware_Burnout', 'Switch')
    print(f"\nEstimated Failure Cost: ${cost['total_estimated_cost']:,.0f}")


if __name__ == "__main__":
    main()
