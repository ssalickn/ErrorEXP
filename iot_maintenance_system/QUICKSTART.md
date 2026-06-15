# Quick Start Guide for IoT Predictive Maintenance System

## ⚡ 5-Minute Setup

### Step 1: Install Dependencies
```bash
cd iot_maintenance_system
pip install -r requirements.txt
```

### Step 2: Run Pipeline (5-10 minutes)
```bash
python run_pipeline.py
```

This generates:
- ✅ Synthetic IoT data for 150 devices (2 years)
- ✅ 60+ engineered features  
- ✅ Trained Weibull AFT model (time-to-failure)
- ✅ Trained XGBoost model (failure modes)

### Step 3: Start API (Terminal 1)
```bash
python -m uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

### Step 4: Start Dashboard (Terminal 2)
```bash
cd src/dashboard
streamlit run app.py
```

## 🌐 Access Services

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:8501 |
| API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

## 📊 What to Try First

### 1. Dashboard Overview
- Go to Dashboard tab
- See system-wide metrics
- View top risk factors

### 2. View Alerts  
- Go to "⚠️ Alerts" tab
- See critical devices
- Read SHAP explanations

### 3. Check Device Details
- Select a device in "🔍 Device Details"
- See telemetry charts
- Review risk factors

### 4. Test API
```bash
curl http://localhost:8000/health

# Make a prediction
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "DEV-0001",
    "device_type": "Switch",
    "zone": "Zone-A",
    "features": {
      "temp_persistence": 0.5,
      "voltage_volatility": 1.2,
      "packet_loss_trend": 0.001,
      "memory_pressure": 0.7,
      "device_age_days": 1000,
      ...
    }
  }'
```

## 🎯 Example Workflows

### Scenario 1: Check Critical Alerts
1. Open Dashboard (http://localhost:8501)
2. Click "⚠️ Alerts"
3. Filter "CRITICAL"
4. Click "View Details" on any device
5. See SHAP explanations

### Scenario 2: Investigate Device Failure
1. Go to "🔍 Device Details"
2. Select a device that failed
3. See temperature/CPU trends
4. View contributing factors
5. Get recommendations

### Scenario 3: Get API Predictions
```bash
# Check model info
curl http://localhost:8000/model-info | jq

# Make batch predictions
curl -X POST http://localhost:8000/predict-batch \
  -H "Content-Type: application/json" \
  -d '[ ... list of devices ... ]' | jq
```

## 📈 Key Metrics to Understand

| Metric | What It Means | Threshold |
|--------|--------------|-----------|
| **Risk Score** | Overall failure likelihood (0-1) | > 0.7 = CRITICAL |
| **TTF (Days)** | Estimated time to failure | < 7 = Urgent |
| **Failure Mode** | Type of failure predicted | See recommendations |
| **Confidence** | How sure the model is (%) | > 80% = Reliable |

## 🔍 Understanding SHAP Values

Each prediction shows top factors. Example:

```
Device: Switch-Zone-B-03
Failure: Hardware_Burnout (87% confidence)

SHAP Values:
  🔴 temp_persistence: +0.42  (INCREASES RISK)
     → Device spent 65% of time > 75°C
  
  🟡 device_age_days: +0.18   (INCREASES RISK)
     → Device is 4.2 years old
  
  🟢 bandwidth_util: -0.05    (DECREASES RISK)
     → Normal network usage
```

**Interpretation:**
- Values > 0: Push prediction toward failure
- Values < 0: Push prediction away from failure
- Magnitude: How much this factor matters

## 🛠️ Customization

### Change Number of Devices
Edit `config/config.yaml`:
```yaml
devices:
  total_devices: 300  # Instead of 150
```

### Change Data Window
```yaml
telemetry:
  history_window_days: 1095  # 3 years instead of 2
```

### Adjust Risk Thresholds
```yaml
alerting:
  risk_thresholds:
    CRITICAL:
      probability_7d: 0.75  # Failure prob
      ttf_days: 7           # Days
```

## ⚠️ Common Issues

### "Models not loaded"
→ Run `python run_pipeline.py` first

### Dashboard crashes
→ Kill port 8501: `lsof -ti:8501 | xargs kill -9`
→ Restart: `streamlit run src/dashboard/app.py`

### API returns 500
→ Check API terminal for error logs
→ Verify `models/` directory has 4 files

### Out of memory
→ Reduce `devices.total_devices` in config.yaml
→ Re-run pipeline

## 📚 Next Steps

1. **Review Results**: Check dashboard and API outputs
2. **Understand Models**: Read SHAP explanations
3. **Customize**: Add your own features/devices
4. **Deploy**: Push to production with monitoring
5. **Retrain**: Monthly with new failure data

## 🎓 Learning Resources

- **Models**: See `src/models/model_trainer.py` for training logic
- **Features**: See `src/data_pipeline/feature_engineer.py` for 60+ indicators
- **Explanations**: See `src/models/explainability.py` for SHAP integration
- **API**: Visit http://localhost:8000/docs (auto-generated docs)

## 📞 Troubleshooting

**Q: How do I interpret predictions?**
A: Look at SHAP factors. They show exactly which features caused the prediction.

**Q: Can I use real data instead of synthetic?**
A: Yes! Replace `data/raw_telemetry.parquet` with your data (same format).

**Q: How often should I retrain?**
A: Monthly when new failures occur (threshold: > 2% F1 improvement).

**Q: Is this suitable for production?**
A: Yes! It's designed for industrial use. Add monitoring, alerting, and logging.

---

**Ready?** Start with:
```bash
python run_pipeline.py
```

Then visit http://localhost:8501 🎯
