"""
FastAPI Backend for Real-Time IoT Predictive Maintenance Inference
Serves predictions with SHAP explanations
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import numpy as np
import pandas as pd
import joblib
import logging
from datetime import datetime
import json

# Import custom modules
import sys
sys.path.insert(0, '../models')
from explainability import ExplainabilityEngine

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Initialize FastAPI app
app = FastAPI(
    title="IoT Predictive Maintenance API",
    description="Real-time failure prediction and root cause analysis",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global models (loaded on startup)
models = {}
explainer = None


@app.on_event("startup")
async def load_models():
    """Load trained models on startup"""
    global models, explainer
    
    logger.info("Loading trained models...")
    
    try:
        models['scaler'] = joblib.load('models/scaler_v1.pkl')
        models['survival'] = joblib.load('models/weibull_aft_v1.pkl')
        models['classification'] = joblib.load('models/xgboost_rca_v1.pkl')
        models['feature_names'] = joblib.load('models/feature_names_v1.pkl')
        
        # Initialize explainability engine
        explainer = ExplainabilityEngine({
            'scaler': 'models/scaler_v1.pkl',
            'classification': 'models/xgboost_rca_v1.pkl',
            'feature_names': 'models/feature_names_v1.pkl'
        })
        
        logger.info("✓ Models loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load models: {e}")
        raise


# ==================== DATA MODELS ====================

class DeviceFeatures(BaseModel):
    """Device feature vector for prediction"""
    device_id: str
    device_type: str
    zone: str
    features: Dict[str, float]  # Feature name -> value
    
    class Config:
        schema_extra = {
            "example": {
                "device_id": "DEV-0001",
                "device_type": "Switch",
                "zone": "Zone-A",
                "features": {
                    "temp_delta_1h": 5.2,
                    "temp_persistence": 0.45,
                    "voltage_volatility": 1.2,
                    "packet_loss_trend": 0.0001,
                }
            }
        }


class PredictionResponse(BaseModel):
    """Risk prediction response"""
    device_id: str
    predicted_failure_mode: str
    confidence: float
    risk_level: str
    failure_probability_7d: float
    estimated_ttf_days: float
    top_factors: List[Dict]
    recommended_actions: List[Dict]


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    models_loaded: bool
    timestamp: str


# ==================== API ENDPOINTS ====================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        models_loaded=bool(models and explainer),
        timestamp=datetime.utcnow().isoformat()
    )


@app.post("/predict", response_model=PredictionResponse)
async def predict_device_failure(device_data: DeviceFeatures):
    """
    Predict device failure mode and time-to-failure
    
    Returns:
        Risk assessment with SHAP explanations and actionable recommendations
    """
    
    if not models or not explainer:
        raise HTTPException(status_code=500, detail="Models not loaded")
    
    try:
        # Build feature vector in correct order
        feature_values = np.array([
            device_data.features.get(fname, 0.0)
            for fname in models['feature_names']
        ])
        
        # Scale features
        X_scaled = models['scaler'].transform(feature_values.reshape(1, -1))
        
        # Stage 1: Survival model (time-to-failure)
        ttf_estimate = models['survival'].predict_median_survival_time(X_scaled)[0]
        survival_prob_7d = models['survival'].predict_survival_function(
            X_scaled, times=[7]
        )[0, 0]
        failure_prob_7d = 1 - survival_prob_7d
        
        # Stage 2: Classification model (failure mode)
        class_proba = models['classification'].predict_proba(X_scaled)[0]
        predicted_class = np.argmax(class_proba)
        predicted_mode = models['classification'].classes_[predicted_class]
        confidence = float(class_proba[predicted_class])
        
        # Get SHAP explanation
        alert = explainer.generate_alert_explanation(
            feature_values,
            device_data.device_id,
            device_data.device_type,
            device_data.zone,
            ttf_estimate,
            failure_prob_7d
        )
        
        # Format response
        response = PredictionResponse(
            device_id=device_data.device_id,
            predicted_failure_mode=alert['predicted_failure_mode'],
            confidence=alert['confidence'],
            risk_level=alert['risk_level'],
            failure_probability_7d=alert['failure_probability_7d'],
            estimated_ttf_days=alert['estimated_ttf_days'],
            top_factors=[
                {
                    'factor': f['feature'],
                    'value': f['value'],
                    'interpretation': f['interpretation'],
                    'impact': '+' if f['shap_value'] > 0 else '-'
                }
                for f in alert['top_contributing_factors']
            ],
            recommended_actions=[
                {
                    'priority': a['priority'],
                    'action': a['action'],
                    'timeline': a['timeline'],
                    'rationale': a['rationale']
                }
                for a in alert['recommended_actions']
            ]
        )
        
        logger.info(f"Prediction for {device_data.device_id}: {response.predicted_failure_mode} "
                   f"({response.confidence:.1%})")
        
        return response
        
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/predict-batch")
async def predict_batch(devices: List[DeviceFeatures]):
    """
    Batch prediction for multiple devices
    
    Args:
        devices: List of DeviceFeatures
        
    Returns:
        List of predictions
    """
    predictions = []
    
    for device in devices:
        try:
            pred = await predict_device_failure(device)
            predictions.append(pred.dict())
        except Exception as e:
            logger.warning(f"Prediction failed for {device.device_id}: {e}")
            predictions.append({
                'device_id': device.device_id,
                'error': str(e)
            })
    
    return {
        'count': len(predictions),
        'successful': sum(1 for p in predictions if 'error' not in p),
        'predictions': predictions
    }


@app.post("/alert-summary")
async def get_alert_summary(devices: List[DeviceFeatures]):
    """
    Get summary of critical alerts across multiple devices
    
    Returns:
        Grouped alerts by risk level
    """
    
    alerts = {
        'CRITICAL': [],
        'HIGH': [],
        'MEDIUM': [],
        'LOW': []
    }
    
    for device in devices:
        try:
            pred = await predict_device_failure(device)
            alerts[pred.risk_level].append({
                'device_id': pred.device_id,
                'failure_mode': pred.predicted_failure_mode,
                'probability': pred.failure_probability_7d,
                'ttf_days': pred.estimated_ttf_days
            })
        except Exception as e:
            logger.warning(f"Prediction failed for {device.device_id}: {e}")
    
    return {
        'timestamp': datetime.utcnow().isoformat(),
        'total_devices': len(devices),
        'critical_count': len(alerts['CRITICAL']),
        'high_count': len(alerts['HIGH']),
        'medium_count': len(alerts['MEDIUM']),
        'alerts': alerts
    }


@app.get("/model-info")
async def get_model_info():
    """Get information about loaded models"""
    
    if not models:
        raise HTTPException(status_code=500, detail="Models not loaded")
    
    return {
        'feature_count': len(models['feature_names']),
        'feature_names': models['feature_names'],
        'survival_model': 'WeibullAFT',
        'classification_model': 'XGBoost',
        'failure_modes': list(models['classification'].classes_),
        'timestamp': datetime.utcnow().isoformat()
    }


# ==================== UTILITY ENDPOINTS ====================

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "IoT Predictive Maintenance API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "predict": "/predict (POST)",
            "predict_batch": "/predict-batch (POST)",
            "alert_summary": "/alert-summary (POST)",
            "model_info": "/model-info"
        }
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
