# 🏭 IoT Predictive Maintenance System - GETTING STARTED

## ✨ You're All Set!

Your complete production-ready IoT predictive maintenance system is ready. Here's what you have:

### 📦 Complete System Includes:
✅ Synthetic data generator (realistic IoT failures)  
✅ 60+ domain-specific features (thermal, power, network, lifecycle)  
✅ Weibull AFT survival analysis (time-to-failure)  
✅ XGBoost multi-class classifier (failure modes)  
✅ SHAP explainability (transparent predictions)  
✅ FastAPI backend (REST inference)  
✅ Streamlit dashboard (interactive monitoring)  
✅ Production-ready code (logging, config, error handling)  

---

## 🚀 QUICKEST START (5 minutes)

### 1. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Complete Pipeline
```bash
python run_pipeline.py
```

This will:
- Generate synthetic IoT data for 150 devices (2 years)
- Compute 60+ features per device
- Train Weibull AFT model (time-to-failure)
- Train XGBoost model (failure modes)
- Save all models and data

**Expected time:** 10-15 minutes

### 3. Start API (Terminal 1)
```bash
python -m uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Start Dashboard (Terminal 2)
```bash
cd src/dashboard
streamlit run app.py
```

### 5. Open Dashboard
```
http://localhost:8501
```

---

## 🎯 What to Do Next

### First: Explore the Dashboard
1. Open http://localhost:8501
2. View system overview (top metrics)
3. Click "⚠️ Alerts" tab
4. Select a device in "🔍 Device Details"
5. See SHAP explanations (why it will fail)

### Second: Test the API
```bash
# Health check
curl http://localhost:8000/health

# API docs
open http://localhost:8000/docs
```

### Third: Understand the System
Read in order:
1. [QUICKSTART.md](QUICKSTART.md) (5 min)
2. [README.md](README.md) (15 min)
3. [INDEX.md](INDEX.md) (5 min)

### Fourth: Customize for Your Devices
Edit [config/config.yaml](config/config.yaml):
```yaml
devices:
  types:
    - "Switch"
    - "Camera"
    - "Sensor"
    - "YourDeviceType"  # Add yours
  total_devices: 150  # Change as needed
```

Then retrain:
```bash
python run_pipeline.py
```

---

## 📊 System Architecture

```
Raw Telemetry (15-min intervals)
        ↓
Feature Engineering (60+ indicators)
        ↓
┌─────────────────────────────────┐
│   Stage 1: Weibull AFT Model    │
│   (Time-to-Failure Prediction)  │
│   → Output: Days until failure  │
└─────────────────────────────────┘
        ↓
┌─────────────────────────────────┐
│ Stage 2: XGBoost Classifier     │
│ (Failure Mode Classification)   │
│ → Output: Hardware, Firmware... │
└─────────────────────────────────┘
        ↓
    SHAP Explanations
    (Why will it fail?)
        ↓
    FastAPI + Streamlit
    (REST API + Dashboard)
        ↓
    ACTIONABLE ALERTS
    (When/why/what to do)
```

---

## 🔍 Key Concepts

### 1. Failure Modes (What breaks?)
- **Hardware Burnout** (35%): Thermal stress accumulation
- **Firmware Crash** (25%): Memory/CPU exhaustion
- **Network Interface** (20%): Port degradation
- **Power Supply** (15%): Voltage instability
- **Environmental** (5%): Cooling, humidity, vibration

### 2. Risk Factors (Why breaks?)
- **Temperature persistence**: Time spent overheated
- **Device age**: Wear-out increases with age
- **Voltage volatility**: Power supply instability
- **Packet loss trend**: Network interface degrading
- **Memory pressure**: Software memory leak

### 3. Predictions (When/How?)
- **TTF (Time-to-Failure)**: Days until predicted failure
- **Failure Probability**: Likelihood within time window
- **SHAP Values**: Feature contribution to prediction
- **Risk Level**: CRITICAL → HIGH → MEDIUM → LOW

---

## 📈 Understanding Dashboard

### Dashboard Tab
Shows system-wide metrics:
- Total devices monitored
- Failure rate
- Average time-to-failure
- Device distribution by type
- Failure distribution by mode

### Alerts Tab
Shows devices at risk:
- Sorted by risk level (CRITICAL first)
- Shows contributing factors
- SHAP values explain why
- Recommended actions

### Device Details Tab
Zooms into one device:
- Live telemetry trends (temperature, CPU, voltage)
- Risk score breakdown
- Failure history
- Recommended actions

### Analytics Tab
Shows patterns:
- Failures by device type
- Failures by zone
- Failure mode distribution
- Trends over time

---

## 💡 Example Prediction

```
Device: Switch-Zone-B-03
Type: Network Switch
Zone: Zone B, Rack 4

RISK ASSESSMENT:
  Risk Level: 🔴 CRITICAL
  Failure Mode: Hardware Burnout (87% confidence)
  Time to Failure: 4.2 days
  7-day Failure Probability: 87%

CONTRIBUTING FACTORS (SHAP Values):
  1. 🌡️  Thermal Stress (+0.42 impact)
     Current: Device spent 18.5 hours > 75°C
     Issue: Cooling system failing
  
  2. 📅 Device Aging (+0.18 impact)
     Current: 4.2 years old
     Issue: Approaching end-of-life

  3. ⚡ Power Instability (+0.12 impact)
     Current: Voltage fluctuating ±3V
     Issue: Power supply degrading

RECOMMENDED ACTIONS:
  [Priority 1] Schedule replacement within 5 days
  [Priority 2] Increase redundancy until replacement
  [Priority 3] Investigate Zone-B cooling system
```

---

## 🔧 Customization Examples

### Add New Device Type
```yaml
# In config/config.yaml
devices:
  types:
    - "Switch"
    - "Camera"
    - "Sensor"
    - "Gateway"  # New!
```

### Change Number of Devices
```yaml
devices:
  total_devices: 300  # Was 150
```

### Adjust Alert Thresholds
```yaml
alerting:
  risk_thresholds:
    CRITICAL:
      probability_7d: 0.75  # Failure probability
      ttf_days: 7           # Days threshold
```

### Add Custom Feature
```python
# In src/data_pipeline/feature_engineer.py
def _compute_custom_indicators(self, device_data):
    return {
        'my_indicator': calculate_from_data(device_data),
    }
```

---

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| [README.md](README.md) | Complete system documentation |
| [QUICKSTART.md](QUICKSTART.md) | 5-minute quick start guide |
| [INDEX.md](INDEX.md) | Full navigation guide |
| [../IoT_RCA_Predictive_Maintenance_Blueprint.md](../IoT_RCA_Predictive_Maintenance_Blueprint.md) | Technical blueprint & theory |

---

## ✅ Verification

Run this to verify everything is set up:
```bash
python test_installation.py
```

Should show:
```
✓ Imports
✓ Directory Structure
✓ File Structure
✓ Configuration
ⓘ Models (empty, will create)
ⓘ Data (empty, will create)

✓ ALL CHECKS PASSED
```

---

## 🎓 Learning Path

**New to the system?** (2-4 hours)
1. Run: `python run_pipeline.py`
2. Explore: Dashboard at http://localhost:8501
3. Read: [QUICKSTART.md](QUICKSTART.md)
4. Try: Different alerts and filters

**Want to customize?** (4-8 hours)
1. Read: [README.md](README.md) sections on customization
2. Edit: `config/config.yaml`
3. Modify: `src/data_pipeline/feature_engineer.py`
4. Retrain: `python run_pipeline.py`

**Going to production?** (8-16 hours)
1. Read: [IoT_RCA_Predictive_Maintenance_Blueprint.md](../IoT_RCA_Predictive_Maintenance_Blueprint.md)
2. Study: ML code in `src/models/`
3. Set up: Logging, monitoring, alerting
4. Deploy: Docker/Kubernetes

---

## 🚨 Troubleshooting

| Problem | Solution |
|---------|----------|
| "Models not found" | Run `python run_pipeline.py` |
| Dashboard won't start | Kill port: `lsof -ti:8501 \| xargs kill -9` |
| API returns 500 | Check logs in terminal where you started it |
| Out of memory | Reduce `devices.total_devices` in config |
| Import errors | Run `pip install -r requirements.txt` again |

---

## 🎯 What to Try First

### Scenario 1: Quick Demo (10 min)
```bash
1. python run_pipeline.py     # Wait ~10 min
2. python -m uvicorn src.api.main:app --reload  # Terminal 1
3. cd src/dashboard && streamlit run app.py      # Terminal 2
4. Open http://localhost:8501
5. Click "⚠️ Alerts" → See critical devices
6. Click device → See SHAP explanations
```

### Scenario 2: Test API (5 min)
```bash
curl http://localhost:8000/health
curl http://localhost:8000/model-info | jq
curl http://localhost:8000/docs  # Open in browser
```

### Scenario 3: Add Your Data (15 min)
```bash
1. Export your IoT data as CSV/Parquet
2. Place in data/raw_telemetry.parquet
3. Run python run_pipeline.py
4. Predictions will use your data!
```

---

## 📞 Questions?

1. **How do I use this with real data?**
   → Replace `data/raw_telemetry.parquet` and retrain

2. **Can I run this in production?**
   → Yes! It's production-ready. Use Docker, add monitoring.

3. **How often should I retrain?**
   → Monthly with new failures (if F1 improves > 2%)

4. **What if predictions are wrong?**
   → Check SHAP explanations to understand drivers, then add better features

5. **How do I integrate with my existing systems?**
   → Use FastAPI endpoint at http://localhost:8000

---

## 🚀 Next Steps

1. **Right Now**: Run `python run_pipeline.py`
2. **In 10 min**: Open http://localhost:8501
3. **In 20 min**: Read [README.md](README.md)
4. **Tomorrow**: Customize for your devices
5. **Next week**: Deploy to production

---

## 📦 What's Included

```
iot_maintenance_system/
├── Data Generation       (Synthetic realistic IoT data)
├── Feature Engineering   (60+ domain-specific indicators)
├── Model Training        (Weibull AFT + XGBoost)
├── Explainability        (SHAP interpretations)
├── FastAPI Backend       (REST inference API)
├── Streamlit Dashboard   (Interactive monitoring)
├── Configuration         (YAML-based customization)
├── Logging               (Production-grade logging)
├── Error Handling        (Robust error recovery)
├── Documentation         (README, quickstart, blueprint)
└── Tests                 (Installation validation)
```

---

## ✨ You're Ready!

Everything is set up. The system is:
- ✅ Complete (all components included)
- ✅ Tested (verification script available)
- ✅ Documented (multiple guides included)
- ✅ Customizable (config-driven + modular code)
- ✅ Production-ready (logging, monitoring, error handling)

**Start with:**
```bash
python run_pipeline.py
```

Then visit: **http://localhost:8501**

---

**Happy predicting!** 🎯🏭
