"""
Synthetic Industrial IoT Data Generator
Generates realistic telemetry with failure modes based on physical/operational degradation
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Tuple, List, Dict
import logging

logger = logging.getLogger(__name__)


class IoTDataGenerator:
    """Generate synthetic IoT device telemetry with realistic failure signatures"""
    
    DEVICE_TYPES = ["Switch", "Camera", "Sensor"]
    ZONES = ["Zone-A", "Zone-B", "Zone-C", "Zone-D"]
    FAILURE_MODES = ["Hardware_Burnout", "Firmware_Crash", "Network_Interface", 
                     "Power_Supply", "Environmental"]
    
    # Operational baselines by device type
    BASELINES = {
        "Switch": {
            "cpu_utilization": 35,
            "memory_used": 45,
            "temperature": 45,
            "packet_loss": 0.1,
            "bandwidth_util": 40,
            "ping_latency": 5,
            "poe_voltage": 48.0,
        },
        "Camera": {
            "cpu_utilization": 60,
            "memory_used": 55,
            "temperature": 50,
            "packet_loss": 0.2,
            "bandwidth_util": 60,
            "ping_latency": 8,
            "poe_voltage": 48.0,
        },
        "Sensor": {
            "cpu_utilization": 20,
            "memory_used": 30,
            "temperature": 40,
            "packet_loss": 0.05,
            "bandwidth_util": 15,
            "ping_latency": 3,
            "poe_voltage": 12.0,
        }
    }
    
    def __init__(self, num_devices: int = 150, days: int = 730):
        self.num_devices = num_devices
        self.days = days
        self.sampling_interval = 15  # minutes
        self.total_observations = (days * 24 * 60) // self.sampling_interval
        
    def generate_complete_dataset(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Generate complete telemetry and failure label datasets
        Returns: (telemetry_df, labels_df)
        """
        logger.info(f"Generating synthetic data for {self.num_devices} devices over {self.days} days...")
        
        all_telemetry = []
        all_failures = []
        
        for device_idx in range(self.num_devices):
            device_id = f"DEV-{device_idx:04d}"
            device_type = self._assign_device_type(device_idx)
            zone = np.random.choice(self.ZONES)
            
            # Device metadata
            install_date = datetime.now() - timedelta(days=np.random.randint(365, 2190))
            firmware_update_date = install_date + timedelta(days=np.random.randint(30, 730))
            
            # Generate device's failure pattern
            failure_time, failure_mode, failure_signature = self._generate_failure_pattern(
                device_type, install_date
            )
            
            # Generate telemetry
            telemetry = self._generate_device_telemetry(
                device_id=device_id,
                device_type=device_type,
                zone=zone,
                install_date=install_date,
                firmware_date=firmware_update_date,
                failure_time=failure_time,
                failure_signature=failure_signature,
                failure_mode=failure_mode
            )
            
            all_telemetry.append(telemetry)
            
            # Record failure (if occurs within observation window)
            if failure_time and failure_time <= datetime.now():
                all_failures.append({
                    'device_id': device_id,
                    'device_type': device_type,
                    'zone': zone,
                    'failure_timestamp': failure_time,
                    'failure_mode': failure_mode,
                    'install_date': install_date,
                    'firmware_date': firmware_update_date
                })
        
        telemetry_df = pd.concat(all_telemetry, ignore_index=True)
        labels_df = pd.DataFrame(all_failures)
        
        logger.info(f"Generated {len(telemetry_df)} telemetry records for {self.num_devices} devices")
        logger.info(f"Total failures: {len(labels_df)}")
        
        return telemetry_df, labels_df
    
    def _assign_device_type(self, index: int) -> str:
        """Assign device type based on distribution"""
        if index % 3 == 0:
            return "Switch"
        elif index % 3 == 1:
            return "Camera"
        else:
            return "Sensor"
    
    def _generate_failure_pattern(
        self, device_type: str, install_date: datetime
    ) -> Tuple[datetime, str, Dict]:
        """
        Determine if/when device fails and its failure mode
        Physical degradation models:
        - Burnout: age + thermal stress → exponential hazard
        - Firmware: age + memory pressure → random
        - Network: packet loss trend + environmental → gradual
        - Power: voltage volatility + age → random shocks
        - Environmental: environmental factors → correlates with zone
        """
        
        device_age_days = (datetime.now() - install_date).days
        
        # Bathtub curve: early failures (5%), wear-out (15%), random (baseline)
        early_failure_prob = 0.02 if device_age_days < 90 else 0
        wear_out_prob = 0.15 if device_age_days > 1095 else 0  # > 3 years
        
        total_failure_prob = early_failure_prob + wear_out_prob + 0.08  # 8% baseline
        
        if np.random.random() > total_failure_prob:
            return None, None, {}  # Device doesn't fail
        
        # Assign failure mode based on probabilities
        failure_mode = np.random.choice(
            self.FAILURE_MODES,
            p=[0.35, 0.25, 0.20, 0.15, 0.05]  # From blueprint
        )
        
        # When does it fail?
        if failure_mode == "Hardware_Burnout":
            # Exponential degradation from thermal stress
            ttf_days = max(7, int(np.random.exponential(scale=20)))
        elif failure_mode == "Firmware_Crash":
            # Random, but earlier if old firmware
            ttf_days = max(3, int(np.random.normal(loc=30, scale=15)))
        elif failure_mode == "Network_Interface":
            # Gradual degradation over weeks
            ttf_days = max(10, int(np.random.exponential(scale=25)))
        elif failure_mode == "Power_Supply":
            # Random shocks, but more likely with age
            ttf_days = max(5, int(np.random.gamma(shape=2, scale=15)))
        else:  # Environmental
            ttf_days = max(10, int(np.random.uniform(10, 60)))
        
        failure_time = datetime.now() - timedelta(days=np.random.randint(1, self.days - ttf_days))
        
        # Failure signature (sensors show degradation pattern)
        failure_signature = {
            'failure_mode': failure_mode,
            'ttf_days': ttf_days,
            'onset_day': ttf_days  # Days before failure when degradation starts
        }
        
        return failure_time, failure_mode, failure_signature
    
    def _generate_device_telemetry(
        self,
        device_id: str,
        device_type: str,
        zone: str,
        install_date: datetime,
        firmware_date: datetime,
        failure_time: datetime,
        failure_signature: Dict,
        failure_mode: str
    ) -> pd.DataFrame:
        """Generate time series telemetry for a single device"""
        
        baselines = self.BASELINES[device_type].copy()
        timestamps = []
        data = {key: [] for key in baselines.keys()}
        # Add environmental/extra fields
        for key in ['ambient_temperature', 'humidity', 'vibration_score']:
            if key not in data:
                data[key] = []
        
        current_time = install_date
        device_age = 0
        
        for obs_idx in range(self.total_observations):
            current_time = install_date + timedelta(minutes=obs_idx * self.sampling_interval)
            
            # Skip observations after now
            if current_time > datetime.now():
                break
            
            device_age_days = (current_time - install_date).days
            
            # Normal operational noise
            cpu_baseline = baselines['cpu_utilization'] + np.random.normal(0, 3)
            memory_baseline = baselines['memory_used'] + np.random.normal(0, 2)
            temp_baseline = baselines['temperature'] + np.random.normal(0, 2)
            
            # Environmental factors
            zone_temp_offset = self._get_zone_temperature_offset(zone, current_time)
            temp_baseline += zone_temp_offset
            
            # Age-related degradation (gradual)
            age_factor = 1.0 + (device_age_days / 1095) * 0.15  # 15% increase over 3 years
            memory_baseline *= age_factor
            
            # Apply failure signature if approaching failure
            poe_voltage_val = baselines['poe_voltage'] + np.random.normal(0, 0.5)
            packet_loss_val = max(0, baselines['packet_loss'] + np.random.normal(0, 0.05))
            ping_latency_val = baselines['ping_latency'] + np.random.normal(0, 1)
            humidity_val = 50 + 15 * np.sin(2 * np.pi * device_age_days / 365)
            vibration_val = max(0, 2 + np.random.normal(0, 0.5))
            
            if failure_time and current_time >= failure_time - timedelta(days=failure_signature['onset_day']):
                days_to_failure = (failure_time - current_time).days
                progress = 1.0 - (days_to_failure / failure_signature['onset_day'])
                
                if failure_mode == "Hardware_Burnout":
                    temp_baseline += 20 * (progress ** 1.5)  # Temperature spikes
                    cpu_baseline += 10 * progress
                    poe_voltage_val = 48.0 - 2 * progress  # Voltage sag
                    
                elif failure_mode == "Firmware_Crash":
                    memory_baseline += 30 * progress  # Memory leak
                    cpu_baseline += 25 * progress  # CPU spike
                    
                elif failure_mode == "Network_Interface":
                    packet_loss_val = baselines['packet_loss'] + 5 * (progress ** 1.2)
                    ping_latency_val = baselines['ping_latency'] + 50 * progress
                    
                elif failure_mode == "Power_Supply":
                    # Voltage fluctuations
                    volatility = 3 * progress
                    poe_voltage_val = 48.0 + np.random.uniform(-volatility, volatility)
                    
                elif failure_mode == "Environmental":
                    humidity_val = 90 * progress  # Moisture buildup
                    vibration_val = 5 * progress
            
            # Cap values to realistic ranges
            cpu_baseline = np.clip(cpu_baseline, 0, 100)
            memory_baseline = np.clip(memory_baseline, 0, 100)
            temp_baseline = np.clip(temp_baseline, 20, 90)
            
            # Append all sensor data once per iteration
            data['poe_voltage'].append(poe_voltage_val)
            data['packet_loss'].append(packet_loss_val)
            data['ping_latency'].append(ping_latency_val)
            
            # Standard observations
            data['cpu_utilization'].append(cpu_baseline)
            data['memory_used'].append(memory_baseline)
            data['temperature'].append(temp_baseline)
            data['bandwidth_util'].append(
                baselines['bandwidth_util'] + np.random.normal(0, 5)
            )
            
            # Environmental
            data['ambient_temperature'].append(
                25 + 10 * np.sin(2 * np.pi * device_age_days / 365)  # Seasonal
            )
            
            data['humidity'].append(humidity_val)
            data['vibration_score'].append(vibration_val)
            
            timestamps.append(current_time)
        
        # Create DataFrame
        df = pd.DataFrame(data)
        df['timestamp'] = timestamps
        df['device_id'] = device_id
        df['device_type'] = device_type
        df['zone'] = zone
        df['install_date'] = install_date
        df['firmware_date'] = firmware_date
        
        if failure_time:
            df['failure_timestamp'] = failure_time
            df['failure_mode'] = failure_mode
        else:
            df['failure_timestamp'] = pd.NaT
            df['failure_mode'] = None
        
        return df[['timestamp', 'device_id', 'device_type', 'zone', 'install_date',
                   'firmware_date', 'failure_timestamp', 'failure_mode'] + 
                  list(baselines.keys())]
    
    def _get_zone_temperature_offset(self, zone: str, timestamp: datetime) -> float:
        """Get zone-specific temperature variations (cooling differences)"""
        zone_temps = {
            "Zone-A": 2,   # Well-cooled
            "Zone-B": -3,  # Hot zone
            "Zone-C": 1,
            "Zone-D": 4    # Extra cooling
        }
        
        offset = zone_temps.get(zone, 0)
        
        # Time-of-day variation (peak load in afternoon)
        hour = timestamp.hour
        offset += 3 * np.sin(2 * np.pi * (hour - 14) / 24)
        
        return offset


def main():
    """Generate and save datasets (or reuse existing)"""
    import os
    
    telemetry_path = 'data/raw_telemetry.parquet'
    labels_path = 'data/labels.parquet'
    
    # Check if data already exists
    if os.path.exists(telemetry_path) and os.path.exists(labels_path):
        logger.info("✓ Reusing existing data files...")
        telemetry_df = pd.read_parquet(telemetry_path)
        labels_df = pd.read_parquet(labels_path)
    else:
        logger.info("Generating new synthetic data...")
        generator = IoTDataGenerator(num_devices=150, days=730)
        telemetry_df, labels_df = generator.generate_complete_dataset()
        
        # Save datasets
        telemetry_df.to_parquet(telemetry_path, index=False)
        labels_df.to_parquet(labels_path, index=False)
    
    print(f"\n✓ Telemetry data: {len(telemetry_df):,} records")
    print(f"✓ Labels data: {len(labels_df)} failures")
    print(f"✓ Failure rate: {len(labels_df) / 150 * 100:.1f}% of devices")
    print(f"\nFailure mode distribution:")
    print(labels_df['failure_mode'].value_counts())


if __name__ == "__main__":
    main()
