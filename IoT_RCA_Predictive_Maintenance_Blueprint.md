# Industrial IoT Predictive Maintenance & Root Cause Analysis Framework
## Comprehensive Technical Blueprint for Switches, Cameras & Sensors

---

## Executive Summary
This framework provides an end-to-end machine learning pipeline for:
1. **Predictive Failure Models** - Estimate remaining useful life (RUL) and failure probability within specific time windows
2. **Root Cause Analysis** - Classify failure modes and identify contributing environmental/operational factors
3. **Explainability & Actionability** - Enable plant technicians to understand *why* and *when* devices fail

---

## Part 1: Data Preprocessing & Feature Engineering

### 1.1 Handling Missing Data from Offline Sensors

**The Challenge**: Devices drop offline, creating sparse, irregular time series with NaN values.

**Strategy by Data Type**:

#### High-Frequency Telemetry (CPU, Memory, Temperature, Bandwidth)
- **Detection**: Mark gaps > 10 minutes as "offline periods" — do NOT interpolate these
- **Forward Fill with Decay**: For gaps < 10 minutes, use exponential forward fill:
  ```
  Last_Value * exp(-λ * time_elapsed)
  λ = 0.1 (tunable based on physics)
  ```
- **Offline Duration Feature**: Create `time_since_last_reading` as a feature (captures device instability)
- **Dropout Rate Feature**: Calculate % of missing readings in trailing 24-hour window
  - High dropout rates (> 30%) correlate with imminent failure

#### Sparse Maintenance Logs
- **Do NOT interpolate failure status** — only use actual observations
- **Create time-based features** instead:
  - `days_since_last_failure` (set to -1 if no prior failure)
  - `time_to_maintenance` (days until next scheduled maintenance)
  - `maintenance_frequency` (failures per 365 days)

#### Environmental Data (Temperature, Humidity, Vibration)
- Use **K-Nearest Neighbors (KNN) imputation** with k=5, weighted by spatial proximity (neighboring zones)
- For PoE voltage: use **linear interpolation** (electrical systems are smooth) only for gaps < 2 hours
- Flag interpolated values with a binary `is_interpolated` feature to inform model uncertainty

**Implementation Priority**:
1. Aggregate data to 15-minute windows (balance resolution with offline gaps)
2. Apply offline masking before any imputation
3. Track imputation methods per sensor as metadata

---

### 1.2 Feature Engineering: Domain-Specific Indicators

#### A. Thermal Stress Indicators (Failure Mode: Hardware Burnout)
```
1. Temperature_Delta_1H = Current_Temp - Mean(Last_1H)
   → Captures sudden heating (fans failing)
   
2. Temperature_Persistence = Count(Temp > 75°C in Last_24H) / 96
   → Measures chronic overheating
   
3. Temp_Over_Threshold_Duration = Sum(time where Temp > T_critical)
   → Cumulative stress metric
   
4. Thermal_Shock_Events = Count(|ΔTemp| > 20°C within 1-hour window)
   → Indicates power cycling, environmental shocks
```

#### B. Power Stability Indicators (Failure Mode: Power Supply Degradation)
```
1. Voltage_Volatility = StdDev(PoE_Voltage, window=1H)
   → High variance suggests failing PSU
   
2. Voltage_Sag_Events = Count(V_min < (V_nominal - 5%) in 24H)
   → Tracks brown-out events
   
3. Power_Draw_Anomaly = (Current_Draw - Rolling_Mean(7D)) / Rolling_Std(7D)
   → Z-score of power consumption (sudden spikes = short circuits)
   
4. PoE_Exhaustion_Index = Total_Draw / PoE_Budget * (Ambient_Temp/20)
   → Temp-adjusted PSU stress
```

#### C. Network Performance Degradation (Failure Mode: Port/Interface Failure)
```
1. Packet_Loss_Trend = Slope(Packet_Loss, window=7D)
   → Positive slope = deteriorating interface
   
2. Latency_Variance = Coefficient_of_Variation(Ping_Latency, window=1H)
   → High variance = network instability, driver issues
   
3. Bandwidth_Utilization_Spike = Rolling_Max(BW_Util, 4H) - Rolling_Mean(BW_Util, 7D)
   → Detects congestion events that stress hardware
   
4. Port_Error_Rate = (Dropped_Packets + CRC_Errors) / Total_Packets
   → Direct indicator of port degradation
```

#### D. Memory & CPU Stress (Failure Mode: Firmware Crash, Memory Corruption)
```
1. Memory_Pressure = (Memory_Used / Memory_Available) with exponential smoothing
   → Captures sustained load
   
2. Memory_Fragmentation_Proxy = Max(Memory_Used) - Current_Memory_Used
   → Unused allocated memory suggests degraded cleanup
   
3. CPU_Throttling_Events = Count(CPU_Freq < Max_Freq for > 5min in 24H)
   → Indicates thermal or power constraints
   
4. Swap_Usage_Rate = Count(Swap_Active) / Total_Observations
   → If > 5%, device is CPU-bound (failing)
```

#### E. Age & Degradation Effects (Device Lifecycle)
```
1. Device_Age_Days = Current_Date - Installation_Date
   → Non-linear (use log-transform for feature importance)
   
2. Time_Since_Last_Failure = Days since last reported failure
   → Reset to 0 on failure; key predictor for wear-out phase
   
3. Firmware_Age_Days = Current_Date - Last_Firmware_Update
   → Stale firmware correlates with exploits, memory leaks
   
4. Maintenance_Gap = Current_Date - Last_Maintenance_Date
   → Unmaintained devices fail faster
```

#### F. Environmental Resilience (Location-Based Risk)
```
1. Heat_Island_Index = (Location_Ambient_Temp - Plant_Avg_Temp) / StdDev
   → Identifies hot zones (cooling inadequacy)
   
2. Humidity_Stress = (Humidity - 45%) ^ 2 if Humidity > 60% or < 30%
   → Corrosion risk (nonlinear penalty)
   
3. Vibration_Cumulative_Energy = Integral(Vibration_Score ^ 2, window=24H)
   → Mechanical fatigue indicator
   
4. Environmental_Stability = Std([Temp, Humidity, Vibration], window=7D)
   → Erratic conditions suggest environmental control failure
```

#### G. Temporal Features (Seasonality & Cycles)
```
1. Hour_of_Day, Day_of_Week (sin/cos encoding)
   → Some devices fail more on weekends (reduced cooling, lower staffing)
   
2. Season = Quantize(Month) → accounts for summer overheating
   
3. Time_in_Shift = (Hour - 6) % 24 relative to plant shift start
   → Peak load hours = higher failure risk
```

#### H. Cross-Device Features (Correlated Failures)
```
1. Zone_Load = Sum(BW_Util, CPU of all devices in Zone)
   → High zone load = shared cooling/power infrastructure stressed
   
2. Device_Cluster_Health = Mean(Health_Score of nearby devices)
   → Indicates if zone has systemic issues
   
3. Same_Model_Failure_Rate = Count(Failures | Device_Model) / Total_Deployed
   → Batch/design defects
```

### 1.3 Time Series Alignment: Bridging Sparse & Dense Data

**Problem**: Telemetry updates every 15 minutes; maintenance logs sparse (every 3-12 months).

**Solution - 3-Tier Aggregation**:
```
Tier 1 (Raw): 15-minute telemetry observations
    ↓
Tier 2 (Rolling Features): Compute rolling stats at 1H, 6H, 24H, 7D windows
    ↓
Tier 3 (Event Alignment): Align Tier 2 with maintenance logs
```

**Label Creation for Supervised Learning**:
```
For each failure event F at time T:
  - POSITIVE class: Observations in [T - 30 days, T) 
    (90 days if RUL > 180 days, use stratified sampling)
  - NEGATIVE class: Random observations from failure-free periods
  - Sampling strategy: 
    1. Stratify by device type, model, zone
    2. Temporal sampling: avoid data leakage
       (test set = most recent failures only)
```

**Handling Delayed Reporting**:
- Maintenance logs often report failures 24-48 hours after occurrence (technician delay)
- Solution: Add `reporting_delay` feature; cross-reference with alert logs if available
- Adjust labels backward by median reporting delay per failure mode

---

## Part 2: Model Selection & Strategy

### 2.1 Two-Stage Pipeline Architecture

```
Raw Telemetry
    ↓
┌─────────────────────────────────────┐
│ Stage 1: Failure Detection & Timing │
│ (Regression/Survival Analysis)      │
│ → Time-to-Failure (TTF)             │
│ → Failure Probability (7D, 30D)     │
└─────────────────────────────────────┘
    ↓
    Only devices with TTF < Threshold
    ↓
┌─────────────────────────────────────┐
│ Stage 2: Failure Mode Classification │
│ (Multi-class Classification)         │
│ → Burnout, Power, Network, etc.     │
└─────────────────────────────────────┘
    ↓
    SHAP → Feature attribution
    ↓
Actionable Alert
```

### 2.2 Stage 1: Time-to-Failure Prediction

#### Model 1A: Weibull Accelerated Failure Time (AFT) Model - **PRIMARY**
**Why Weibull for Industrial Equipment**:
- Weibull naturally models device wear-out (bathtub curve)
- Handles censoring (devices still operational at observation end)
- Shape parameter k indicates failure mode:
  - k < 1: Early-life failures (defects)
  - k ≈ 1: Random failures (environmental shocks)
  - k > 1: Wear-out phase (age-related)

**Implementation**:
```python
from lifelines import WeibullAFTFitter
from lifelines.utils import median_survival_times

# Time = days until failure (censored if no failure observed)
T = df['days_to_failure'] | df['observation_duration']
E = df['failure_observed'] (1 = failure, 0 = censored)

model = WeibullAFTFitter()
model.fit(X_train, T_train, E_train)

# Per-device predictions
survival_prob_7d = model.predict_survival_function(X_test, times=[7])
median_ttf = model.predict_median_survival_time(X_test)

# Risk score: inverse of median TTF
risk_score = 1 / (1 + median_ttf / median(training_ttf))
```

**Key Advantages**:
- Handles right-censoring natively (devices still operating)
- Interpretable shape parameter per device type
- Faster than Cox models, parallelizable

**Limitations**:
- Assumes proportional hazards (may not hold for environmental shocks)
- Requires failure labels (but RCA logs provide this)

#### Model 1B: Gradient Boosted Survival (LightGBM with Custom Objective) - **SECONDARY**
**Why for complex interactions**:
- Captures non-linear relationships (e.g., temp + humidity + age = exponential risk)
- Handles missing data gracefully
- Fast training on large datasets

**Implementation**:
```python
import lightgbm as lgb

# Custom survival loss (Cox partial likelihood)
train_data = lgb.Dataset(X_train, label=T_train, 
                         group=[sum(df.device_id == device_id)])

model = lgb.train(
    params={'objective': 'cox', 'metric': 'cox_lambda_loss'},
    train_set=train_data,
    num_boost_round=500
)

# Partial hazard scores (higher = sooner failure)
hazard_scores = model.predict(X_test)
```

#### Model 1C: 1D CNN LSTM for Time Series RUL - **TERTIARY (high-frequency sensors)**
**When to use**:
- For devices with dense telemetry (e.g., thermal cameras, power sensors every 5 min)
- Captures temporal degradation patterns autonomous

**Architecture**:
```
Input (sequence of 288 15-min observations = 3-day window)
    ↓
1D Conv layers (filters=[32, 64], kernel=7, stride=1)
    ↓
LSTM (units=64, return_sequences=True)
    ↓
Attention layer (weights recent observations)
    ↓
Dense(1) → Sigmoid → Failure_Probability_Next_7D
```

**Advantages**:
- Captures temporal patterns (gradual degradation)
- Robust to sensor noise via convolution
- Can integrate attention weights for explainability

**Limitations**:
- Requires significant labeled data (> 5K failure sequences)
- Black-box (SHAP integration needed for explainability)

---

### 2.3 Stage 2: Failure Mode Classification (Multi-Class)

**Class Distribution (Typical)**:
- Hardware Burnout: 35%
- Firmware Crash/Software: 25%
- Network Interface Failure: 20%
- Power Supply Issues: 15%
- Environmental/Unknown: 5%

**Handling Severe Class Imbalance**:

#### Technique 1: Stratified Weighted Loss
```python
from sklearn.utils.class_weight import compute_class_weight

weights = compute_class_weight(
    'balanced',
    classes=np.unique(y_train),
    y=y_train
)
# Normalize: rare classes get up to 4-5x weight

model = XGBClassifier(scale_pos_weight=weights[1], max_depth=6)
```

#### Technique 2: SMOTE + Tomek Links (Hybrid Resampling)
```python
from imblearn.pipeline import Pipeline
from imblearn.over_sampling import SMOTE
from imblearn.under_sampling import TomekLinks

pipeline = Pipeline([
    ('smote', SMOTE(sampling_strategy=0.5, k_neighbors=5)),
    ('tomek', TomekLinks(sampling_strategy='majority')),
    ('clf', XGBClassifier(random_state=42))
])
```

#### Technique 3: Focal Loss (for neural networks)
```python
# For imbalanced datasets, penalize easy negatives
focal_loss = -α * (1 - p_t)^γ * log(p_t)
# α = class weights, γ = 2 (standard)
```

#### Model Selection for Multi-Class RCA

| Model | Reason | Pros | Cons |
|-------|--------|------|------|
| **XGBoost** | Non-linear feature interactions | Fast, handles imbalance, GPU support | Hyperparameter tuning needed |
| **Random Forest** | Baseline with feature importances | Interpretable, stable | Slower, less precise |
| **Logistic Regression (OvR)** | Baseline linear model | Ultra-fast, stable baselines | Low accuracy on imbalanced data |
| **Neural Network (3-layer)** | If enough labeled failures (> 3K) | High capacity | Requires SHAP for explainability |

**Recommended: XGBoost with Stratified K-Fold + Class Weights**
```python
from sklearn.model_selection import StratifiedKFold
from xgboost import XGBClassifier

skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

for train_idx, val_idx in skf.split(X, y):
    X_tr, X_val = X[train_idx], X[val_idx]
    y_tr, y_val = y[train_idx], y[val_idx]
    
    model = XGBClassifier(
        n_estimators=500,
        max_depth=6,
        learning_rate=0.05,
        scale_pos_weight={minority_class_weight},
        eval_metric='mlogloss',  # For multi-class
        early_stopping_rounds=50
    )
    model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)
```

---

### 2.4 Hyperparameter Tuning Strategy

**Use Bayesian Optimization, NOT Grid Search** (saves 10-50x compute):
```python
from optuna import create_study, samplers

def objective(trial):
    max_depth = trial.suggest_int('max_depth', 4, 12)
    learning_rate = trial.suggest_float('learning_rate', 0.01, 0.3, log=True)
    min_child_weight = trial.suggest_int('min_child_weight', 1, 10)
    
    model = XGBClassifier(max_depth=max_depth, 
                          learning_rate=learning_rate,
                          min_child_weight=min_child_weight)
    
    scores = cross_validate(model, X_train, y_train, cv=5, scoring='f1_weighted')
    return scores['test_score'].mean()

study = create_study(direction='maximize', 
                     sampler=samplers.TPESampler(seed=42))
study.optimize(objective, n_trials=100, show_progress_bar=True)
```

---

## Part 3: Root Cause Analysis & Explainability

### 3.1 SHAP (SHapley Additive exPlanations) - Primary Explainability Tool

**Why SHAP for Industrial RCA**:
- Quantifies each feature's contribution to a specific prediction
- Theoretically sound (Shapley values from game theory)
- Generates human-understandable explanations for technicians

#### SHAP for Failure Mode Classification (Stage 2)

```python
import shap

# Train model (XGBoost)
model = XGBClassifier(...)
model.fit(X_train, y_train)

# Create explainer
explainer = shap.TreeExplainer(model)
shap_values = explainer.shap_values(X_test)

# Example: Device that predicted "Hardware Burnout"
device_idx = 42
class_burnout = 0  # Index for Hardware Burnout class

# Plot for a single prediction
shap.force_plot(
    explainer.expected_value[class_burnout],
    shap_values[class_burnout][device_idx],
    X_test.iloc[device_idx],
    feature_names=feature_names
)
```

**Output to Technician**:
```
Device: Switch-Zone-B-03
Alert: 87% Risk of Hardware Burnout in 7 Days

Top Contributing Factors:
  🔴 Temperature_Persistence (Last 24H): +42% risk
     (Device spent 18.5 hours > 75°C; cooling failure?)
  
  🟡 Device_Age_Days: +18% risk
     (Device is 4.2 years old; approaching end-of-life)
  
  🟡 PoE_Voltage_Volatility: +12% risk
     (Power supply fluctuating ±3V; PSU degrading?)
  
  🟢 Recent_Maintenance: -15% risk
     (Cleaned/serviced 3 weeks ago, but degrading fast)

Recommended Action: 
  → Schedule replacement within 5 days
  → Check cooling system in Zone-B
  → Consider redundancy until replacement
```

### 3.2 LIME for Local Interpretable Model-Agnostic Explanations

**Use LIME when**:
- Model is a black-box neural network
- Need to challenge SHAP results for validation

```python
from lime.tabular import LimeTabularExplainer

explainer = LimeTabularExplainer(
    X_train,
    feature_names=feature_names,
    class_names=['Burnout', 'Firmware', 'Network', 'Power', 'Unknown'],
    mode='classification'
)

# Explain single prediction
exp = explainer.explain_instance(
    X_test.iloc[42],
    model.predict_proba,
    num_features=10
)

# LIME approximates prediction with simple linear model
# Weights show local feature importance
exp.show_in_notebook()
```

**LIME vs. SHAP**:
| Aspect | SHAP | LIME |
|--------|------|------|
| Speed | Fast (XGBoost/Tree models) | ~1 sec per prediction |
| Stability | Theoretically sound | May vary (samples-dependent) |
| Interpretability | Force/summary plots | Bar chart of weights |
| Use Case | Default choice | Validation, NN models |

---

### 3.3 Permutation Feature Importance (Model-Agnostic)

**Capture global feature importance across all failures**:
```python
from sklearn.inspection import permutation_importance

result = permutation_importance(
    model, X_test, y_test,
    n_repeats=30,
    random_state=42,
    n_jobs=-1
)

# Plot top features by importance
importance_df = pd.DataFrame({
    'Feature': feature_names,
    'Importance_Mean': result.importances_mean,
    'Importance_Std': result.importances_std
}).sort_values('Importance_Mean', ascending=False)

print(importance_df.head(15))
```

**Interpretation**: If shuffling `Temperature_Persistence` drops F1-score by 0.12, it's responsible for 12% of model's predictive power.

---

### 3.4 Residual Analysis for Model Failures

**Understand when your model gets it wrong**:
```python
# Classify predictions into 4 buckets
y_pred = model.predict(X_test)
y_pred_proba = model.predict_proba(X_test)

TP = (y_pred == y_test) & (y_test == 1)
FP = (y_pred == 1) & (y_test == 0)
TN = (y_pred == y_test) & (y_test == 0)
FN = (y_pred == 0) & (y_test == 1)  # CRITICAL: Missed failures

# Analyze FN (false negatives - missed burnout predictions)
fn_devices = X_test[FN]
print(f"FN Devices had avg Temperature_Persistence: {fn_devices['Temperature_Persistence'].mean()}")
# → May indicate bimodal failure signatures model missed
```

---

## Part 4: Actionable Outputs & Alerting

### 4.1 Prediction Output Schema

Each prediction should generate a structured alert:

```json
{
  "device_id": "Switch-Zone-B-03",
  "device_type": "Network Switch",
  "model": "Cisco Catalyst 9500",
  "location": "Zone B, Rack 4",
  
  "risk_assessment": {
    "failure_probability_7d": 0.87,
    "failure_probability_30d": 0.96,
    "estimated_days_to_failure": 4.2,
    "confidence_interval_95": [2.1, 8.5]
  },
  
  "predicted_failure_mode": {
    "primary": "Hardware Burnout",
    "probability": 0.65,
    "secondary": ["Power Supply Degradation", 0.22],
    "tertiary": ["Firmware Crash", 0.13]
  },
  
  "root_cause_factors": [
    {
      "factor": "Temperature_Persistence",
      "current_value": "18.5 hours > 75°C",
      "shap_contribution": "+42%",
      "severity": "CRITICAL",
      "interpretation": "Device cooling system failing; thermal accumulation detected"
    },
    {
      "factor": "Device_Age_Days",
      "current_value": "1534 days (4.2 years)",
      "shap_contribution": "+18%",
      "severity": "HIGH",
      "interpretation": "Device exceeds typical 4-year lifespan; wear-out phase"
    },
    {
      "factor": "PoE_Voltage_Volatility",
      "current_value": "±3.2V fluctuation",
      "shap_contribution": "+12%",
      "severity": "MEDIUM",
      "interpretation": "Power supply instability; may trigger cascading failures"
    }
  ],
  
  "recommended_actions": [
    {
      "priority": 1,
      "action": "Schedule device replacement",
      "timeline": "Within 5 days",
      "rationale": "High failure probability (87%) with imminent timeline"
    },
    {
      "priority": 2,
      "action": "Increase redundancy",
      "timeline": "Immediate",
      "rationale": "Until replacement, route traffic away if possible"
    },
    {
      "priority": 3,
      "action": "Investigate Zone B cooling",
      "timeline": "Within 24 hours",
      "rationale": "Temperature persistence unusual; potential infrastructure issue"
    }
  ],
  
  "similar_past_failures": [
    {
      "device": "Switch-Zone-A-01",
      "outcome": "Failed 6 days after similar alert",
      "failure_mode": "Power supply burnout",
      "time_to_failure_actual": 6.1
    }
  ],
  
  "model_metadata": {
    "model_version": "v2.3-stage1",
    "last_retraining": "2026-05-15",
    "training_accuracy": 0.89,
    "training_f1_weighted": 0.87,
    "prediction_timestamp": "2026-06-15T14:32:00Z"
  }
}
```

### 4.2 Alert Routing & Escalation

```python
class AlertEscalation:
    def __init__(self):
        self.risk_thresholds = {
            'CRITICAL': (0.75, 7),      # P(failure 7d) > 75%
            'HIGH': (0.50, 14),         # P(failure 14d) > 50%
            'MEDIUM': (0.30, 30),       # P(failure 30d) > 30%
            'LOW': (0.10, 90)           # P(failure 90d) > 10%
        }
    
    def generate_alert(self, prediction):
        risk_level = self._classify_risk(prediction)
        
        if risk_level == 'CRITICAL':
            recipients = ['ops_manager', 'lead_technician']
            notification_method = ['sms', 'email', 'dashboard']
            sla_hours = 4  # Escalate to management if unacknowledged
            
        elif risk_level == 'HIGH':
            recipients = ['technician_assigned_to_zone']
            notification_method = ['email', 'dashboard']
            sla_hours = 24
            
        elif risk_level == 'MEDIUM':
            recipients = ['asset_management_system']
            notification_method = ['dashboard']
            sla_hours = 72
        
        return AlertPackage(
            risk_level=risk_level,
            device_id=prediction['device_id'],
            recipients=recipients,
            sla_hours=sla_hours,
            action_items=prediction['recommended_actions']
        )

    def _classify_risk(self, prediction):
        p_7d = prediction['risk_assessment']['failure_probability_7d']
        
        if p_7d > 0.75:
            return 'CRITICAL'
        elif p_7d > 0.50:
            return 'HIGH'
        elif p_7d > 0.30:
            return 'MEDIUM'
        else:
            return 'LOW'
```

### 4.3 Dashboard Visualization

**Real-Time Operations Dashboard**:
```
┌─────────────────────────────────────────────────────────────┐
│ Industrial IoT Risk Monitoring Dashboard                     │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│ 🔴 CRITICAL ALERTS: 3                                        │
│    • Switch-Zone-B-03:     87% risk (4.2 days)              │
│    • Camera-Zone-D-12:     79% risk (5.1 days)              │
│    • Sensor-Zone-A-08:     76% risk (2.8 days)              │
│                                                               │
│ 🟡 HIGH ALERTS: 7                                            │
│                                                               │
│ 🟢 MEDIUM ALERTS: 19                                         │
│                                                               │
├─────────────────────────────────────────────────────────────┤
│ Top Risk Factors (All Devices):                              │
│                                                               │
│ 1. Temperature_Persistence    [████████░░] 28% of failures   │
│ 2. Device_Age_Days            [██████░░░░] 19% of failures   │
│ 3. PoE_Voltage_Volatility     [█████░░░░░] 15% of failures   │
│ 4. Packet_Loss_Trend          [████░░░░░░] 12% of failures   │
│ 5. Memory_Pressure            [███░░░░░░░]  9% of failures   │
│                                                               │
├─────────────────────────────────────────────────────────────┤
│ Failure Mode Distribution:                                   │
│                                                               │
│ Hardware Burnout     [████████████████░░░] 58%  (← Dominant) │
│ Firmware Crash       [████████░░░░░░░░░░] 22%                │
│ Network Interface    [██████░░░░░░░░░░░░] 15%                │
│ Power Supply         [█████░░░░░░░░░░░░░] 12%                │
│ Environmental        [██░░░░░░░░░░░░░░░░]  3%                │
│                                                               │
└─────────────────────────────────────────────────────────────┘

Detail View (Example):
┌──────────────────────────────┐
│ Device: Switch-Zone-B-03     │
│                              │
│ Risk Timeline:               │
│ 7D:   87% ████████           │
│ 14D:  94% █████████          │
│ 30D:  98% ██████████         │
│                              │
│ Primary Cause: Overheating   │
│ Temp (24H): 21.3°C avg,      │
│ Peak: 82°C (sustained)       │
│                              │
│ [View SHAP Analysis] [TTF]   │
│ [Similar Failures] [Actions] │
└──────────────────────────────┘
```

---

## Part 5: Model Training Pipeline & Deployment

### 5.1 End-to-End Training Workflow

```python
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
import xgboost as xgb
from lifelines import WeibullAFTFitter
import shap

class IoTFailurePredictionPipeline:
    
    def __init__(self, config_path='config.yaml'):
        self.config = self._load_config(config_path)
        self.feature_scaler = StandardScaler()
        self.survival_model = WeibullAFTFitter()
        self.classification_model = None
        
    def execute_full_training(self, raw_data_path):
        """
        End-to-end pipeline: Raw Data → Features → Models → Validation
        """
        print("[1/6] Loading raw telemetry & maintenance logs...")
        df = pd.read_parquet(raw_data_path)
        
        print("[2/6] Data cleaning & imputation...")
        df = self._clean_and_impute(df)
        
        print("[3/6] Feature engineering...")
        X_features = self._engineer_features(df)
        
        print("[4/6] Creating labels for supervised learning...")
        y_failure_time, y_failure_observed, y_failure_mode = self._create_labels(df)
        
        print("[5/6] Training Stage 1 (Time-to-Failure)...")
        self._train_survival_model(X_features, y_failure_time, y_failure_observed)
        
        print("[6/6] Training Stage 2 (Failure Mode Classification)...")
        self._train_classification_model(X_features, y_failure_mode)
        
        print("✓ Training complete. Running validation...")
        self._validate_models(X_features, y_failure_mode)
        
        return self
    
    def _clean_and_impute(self, df):
        """Handle missing data per data type"""
        # Mark offline periods (> 10 min gap)
        df['is_offline'] = df.groupby('device_id')['timestamp'].diff() > pd.Timedelta(minutes=10)
        
        # Forward fill high-freq telemetry only within 10-min gaps
        for col in ['cpu_utilization', 'memory_used', 'temperature', 'bandwidth_util']:
            df[col] = df.groupby('device_id')[col].transform(
                lambda x: x.fillna(method='ffill', limit=1)
            )
        
        # KNN imputation for environmental data (spatial proximity)
        # [Implementation details...]
        
        return df.dropna(subset=['temperature', 'bandwidth_util'], thresh=0.8)
    
    def _engineer_features(self, df):
        """Compute domain-specific features"""
        features = pd.DataFrame()
        
        for device_id in df['device_id'].unique():
            device_df = df[df['device_id'] == device_id].sort_values('timestamp')
            
            # Thermal indicators
            features.loc[device_id, 'temp_delta_1h'] = (
                device_df['temperature'].iloc[-1] - 
                device_df['temperature'].iloc[-96:].mean()  # 96 = 1 hour at 15-min intervals
            )
            
            features.loc[device_id, 'temp_persistence'] = (
                (device_df['temperature'] > 75).sum() / len(device_df)
            )
            
            # Power indicators
            features.loc[device_id, 'voltage_volatility'] = (
                device_df['poe_voltage'].iloc[-96:].std()
            )
            
            # Network indicators
            features.loc[device_id, 'packet_loss_trend'] = (
                np.polyfit(range(len(device_df.iloc[-168:])), 
                          device_df['packet_loss'].iloc[-168:], 1)[0]  # Last 7 days
            )
            
            # Age & maintenance
            features.loc[device_id, 'device_age_days'] = (
                (device_df['timestamp'].max() - device_df['install_date'].iloc[0]).days
            )
            
            features.loc[device_id, 'days_since_last_failure'] = (
                (device_df['timestamp'].max() - device_df['last_failure_date'].iloc[0]).days
                if device_df['last_failure_date'].notna().any() else -1
            )
        
        return features
    
    def _create_labels(self, df):
        """Create supervised learning labels from maintenance logs"""
        y_time = []
        y_observed = []
        y_mode = []
        
        for device_id in df['device_id'].unique():
            device_failures = df[(df['device_id'] == device_id) & 
                               (df['failure_timestamp'].notna())]
            
            if len(device_failures) > 0:
                # Time-to-event from last observation to failure
                last_obs = df[df['device_id'] == device_id]['timestamp'].max()
                failure_time = device_failures['failure_timestamp'].iloc[-1]
                
                y_time.append((failure_time - last_obs).days)
                y_observed.append(1)  # Failure occurred
                y_mode.append(device_failures['failure_mode'].iloc[-1])
            else:
                # Censored: device still operational
                last_obs = df[df['device_id'] == device_id]['timestamp'].max()
                observation_duration = (last_obs - df[df['device_id'] == device_id]['timestamp'].min()).days
                
                y_time.append(observation_duration)
                y_observed.append(0)  # Censored
                y_mode.append('No_Failure')  # Unknown mode
        
        return np.array(y_time), np.array(y_observed), np.array(y_mode)
    
    def _train_survival_model(self, X, y_time, y_observed):
        """Train Weibull AFT for time-to-failure prediction"""
        X_scaled = self.feature_scaler.fit_transform(X)
        
        self.survival_model.fit(
            X_scaled,
            T=y_time,
            E=y_observed,
            show_progress=True
        )
        
        # Log Weibull shape parameters by device type
        for device_type in X['device_type'].unique():
            mask = X['device_type'] == device_type
            shape = self.survival_model.lambda_[mask].mean()
            print(f"  {device_type}: Weibull shape={shape:.2f}")
    
    def _train_classification_model(self, X, y):
        """Train XGBoost for failure mode classification"""
        from sklearn.utils.class_weight import compute_class_weight
        
        X_scaled = self.feature_scaler.transform(X)
        X_train, X_val, y_train, y_val = train_test_split(
            X_scaled, y, test_size=0.2, stratify=y, random_state=42
        )
        
        class_weights = compute_class_weight(
            'balanced',
            classes=np.unique(y_train),
            y=y_train
        )
        weight_dict = {c: class_weights[c] for c in np.unique(y_train)}
        
        self.classification_model = xgb.XGBClassifier(
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            scale_pos_weight=weight_dict,
            early_stopping_rounds=50,
            random_state=42
        )
        
        self.classification_model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False
        )
    
    def _validate_models(self, X, y):
        """Cross-validation & performance metrics"""
        from sklearn.model_selection import cross_validate
        from sklearn.metrics import f1_score, precision_score, recall_score
        
        X_scaled = self.feature_scaler.transform(X)
        
        # Classification metrics
        cv_results = cross_validate(
            self.classification_model, X_scaled, y,
            cv=5,
            scoring=['f1_weighted', 'precision_weighted', 'recall_weighted']
        )
        
        print("\n=== Classification Model Performance ===")
        print(f"F1 Score (Weighted):   {cv_results['test_f1_weighted'].mean():.3f}")
        print(f"Precision (Weighted):  {cv_results['test_precision_weighted'].mean():.3f}")
        print(f"Recall (Weighted):     {cv_results['test_recall_weighted'].mean():.3f}")
        
        # Feature importance
        feature_importance = pd.DataFrame({
            'feature': X.columns,
            'importance': self.classification_model.feature_importances_
        }).sort_values('importance', ascending=False)
        
        print("\n=== Top 10 Features by Importance ===")
        print(feature_importance.head(10).to_string(index=False))

# Usage
pipeline = IoTFailurePredictionPipeline('config/default.yaml')
pipeline.execute_full_training('data/raw_telemetry_2024-2026.parquet')

# Save models for production
import joblib
joblib.dump(pipeline.survival_model, 'models/weibull_aft_v1.pkl')
joblib.dump(pipeline.classification_model, 'models/xgboost_rca_v1.pkl')
joblib.dump(pipeline.feature_scaler, 'models/scaler_v1.pkl')
```

### 5.2 Real-Time Inference Pipeline

```python
class RealTimeAlertingSystem:
    
    def __init__(self, model_paths):
        self.survival_model = joblib.load(model_paths['survival'])
        self.classification_model = joblib.load(model_paths['classification'])
        self.scaler = joblib.load(model_paths['scaler'])
        self.shap_explainer = shap.TreeExplainer(self.classification_model)
    
    def process_device_telemetry(self, device_id, latest_telemetry):
        """
        Called every 15 minutes with latest device metrics
        Returns: Alert object (if risk exceeds threshold)
        """
        # Compute rolling features from telemetry buffer
        features = self._compute_features(device_id, latest_telemetry)
        X = pd.DataFrame([features])
        X_scaled = self.scaler.transform(X)
        
        # Stage 1: Estimate time-to-failure
        ttf_estimate = self.survival_model.predict_median_survival_time(X_scaled)[0]
        survival_prob_7d = self.survival_model.predict_survival_function(X_scaled, times=[7])[0]
        
        if survival_prob_7d < 0.25:  # < 25% chance of surviving 7 days
            # Stage 2: Predict failure mode
            failure_mode_proba = self.classification_model.predict_proba(X_scaled)[0]
            failure_mode = self.classification_model.predict(X_scaled)[0]
            
            # SHAP explanation
            shap_values = self.shap_explainer.shap_values(X_scaled)
            
            alert = self._package_alert(
                device_id=device_id,
                ttf=ttf_estimate,
                failure_prob_7d=1 - survival_prob_7d,
                failure_mode=failure_mode,
                failure_mode_proba=failure_mode_proba,
                shap_values=shap_values,
                features=X
            )
            
            return alert
        
        return None  # No alert
    
    def _package_alert(self, device_id, ttf, failure_prob_7d, failure_mode, 
                       failure_mode_proba, shap_values, features):
        """Create structured alert JSON"""
        # [Implementation of alert creation logic from Part 4.1]
        pass
```

---

## Part 6: Testing, Validation & Iteration

### 6.1 Model Evaluation Metrics by Stage

#### Stage 1 (Survival Analysis)
```
Concordance Index (C-index): 0.78 - 0.85
  → Measures ranking ability: higher is better
  → If model predicts device X fails before Y, is that true?

Calibration Plot:
  → Predicted 7-day failure prob vs. actual failure rate
  → Points on diagonal = well-calibrated
  → Brier Score: Mean squared error of probabilities
```

#### Stage 2 (Classification)
```
Weighted F1-Score (handles imbalance): 0.82 - 0.88
Precision per class (false alarms):     0.85 - 0.92
Recall per class (missed failures):     0.75 - 0.85
ROC-AUC (one-vs-rest):                 0.89 - 0.95
```

### 6.2 A/B Testing (Human-in-the-Loop)

```
Timeline: 4 weeks
─────────────────
Week 1: Deploy alongside existing maintenance rules
        (don't action on ML alerts, only log)

Week 2: Compare predictions vs. actual failures
        - False positive rate (unnecessary alerts)
        - True positive rate (caught failures)
        - Missed failures (recall)

Week 3: Technician feedback survey
        - Are SHAP explanations clear?
        - False alert causes? (retrain)
        - Actionability score

Week 4: Gradually route to technician queue
        - Monitor alert escalation rate
        - Adjust thresholds based on feedback
```

### 6.3 Feedback Loop for Continuous Improvement

```
Monthly Retraining Process:
1. Collect new failures from last 30 days
2. Verify failure modes (eliminate data entry errors)
3. Retrain Weibull AFT + XGBoost with expanded dataset
4. Compare performance metrics against previous version
   - If F1 improves > 2%: deploy
   - If precision drops > 3%: investigate (data quality)
5. Update SHAP explanations in dashboard
6. Analyze false negatives (missed failures)
   - New failure signature?
   - New device type/model?
   - → Add as new feature or model class
```

---

## Part 7: Implementation Roadmap (6-Month Plan)

### Phase 1: Foundation (Weeks 1-4)
- [x] Data pipeline: ETL from sensor networks to data lake
- [x] Feature engineering library (template-based for new device types)
- [x] Stage 1: Weibull AFT model, basic validation
- [ ] Dashboard prototype (read-only, no alerts)

### Phase 2: Production-Ready (Weeks 5-10)
- [ ] Stage 2: XGBoost multi-class model + SHAP integration
- [ ] Alert system with tiering (CRITICAL → LOW)
- [ ] A/B testing: silent mode (log only)
- [ ] Technician training on dashboard/SHAP explanations

### Phase 3: Optimization (Weeks 11-15)
- [ ] Transition to active alerts (low-risk thresholds first)
- [ ] Automated hyperparameter tuning (Bayesian Optimization)
- [ ] Advanced features: cross-device correlation, zone-level anomalies
- [ ] Mobile app for technician on-the-go alerts

### Phase 4: Scale & Embed (Weeks 16-24)
- [ ] Extend to all device types (currently: switches, cameras, core sensors)
- [ ] Real-time inference at edge (model on gateway devices)
- [ ] Integration with ticketing system (auto-create work orders)
- [ ] Feedback mechanism: technician tags true/false positives
- [ ] Monthly retraining automation

---

## Key Takeaways for Technical Implementation

1. **Two-Stage Pipeline**: Time-to-failure (survival) first, then failure-mode classification
   - Reduces false positives by filtering low-risk devices
   - Focuses SHAP computation on actionable alerts

2. **Feature Engineering is 70% of Success**:
   - Domain-specific indicators (thermal stress, power stability) > generic ML features
   - Temporal aggregation critical for sparse/dense data fusion

3. **Class Imbalance Handling**:
   - Use weighted loss + SMOTE for robust multi-class RCA
   - Focal loss for neural networks

4. **Explainability is Non-Negotiable**:
   - SHAP for model transparency
   - Technicians need to understand *why* before they act
   - Automated explanations reduce decision time

5. **Validation Gaps Cause Failures**:
   - Test on held-out recent failures (temporal split)
   - Monitor false negatives obsessively (missed predictions = unplanned downtime)
   - A/B test extensively before full rollout

---

## References & Further Reading

- **Weibull Analysis**: Abernethy, R. B. *The New Weibull Handbook* (Reliability & statistical analysis)
- **SHAP**: Lundberg, S. M., & Lee, S. I. (2017) "A Unified Approach to Interpreting Model Predictions"
- **Class Imbalance**: He, H., & Garcia, E. A. (2009) "Learning from Imbalanced Data"
- **Predictive Maintenance**: ISO 13374 (industrial condition monitoring standards)
- **IIoT Standards**: IEC 61508 (functional safety), OPC UA (industrial data exchange)

---

## Appendix: Quick Reference - Feature List

| Feature Name | Type | Computation | Failure Correlation |
|--------------|------|-----------|-------------------|
| Temperature_Persistence | Thermal | % time > 75°C (24h) | Burnout (0.72) |
| Voltage_Volatility | Power | StdDev(PoE_V, 1h) | Power Issues (0.65) |
| Packet_Loss_Trend | Network | Slope(loss, 7d) | Interface Fail (0.68) |
| Memory_Pressure | CPU | Used / Available | Firmware Crash (0.58) |
| Device_Age_Days | Lifecycle | Days since install | Burnout (0.61) |
| Days_Since_Last_Failure | Lifecycle | Days from last failure | Burnout (0.55) |
| Heat_Island_Index | Environmental | (Zone_Temp - Avg) / Std | Burnout (0.44) |
| Temp_Delta_1H | Thermal | Current - Mean(1h) | Burnout (0.50) |
| Vibration_Energy | Environmental | Integral(Vib²) | Interface Fail (0.42) |
| Firmware_Age_Days | Lifecycle | Days since update | Firmware Crash (0.63) |

---

**Document Version**: 1.0  
**Last Updated**: June 2026  
**Author**: Industrial IoT Reliability Engineering Team
