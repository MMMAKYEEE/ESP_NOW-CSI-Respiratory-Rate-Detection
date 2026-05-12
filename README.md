# ESP_NOW-CSI-Respiratory-Rate-Detection

## 1. Project Overview

This project is a Wi-Fi CSI based breathing and small-motion detection system using three ESP32-C5 devices.

The system uses:

- One ESP32-C5 as the transmitter
- Two ESP32-C5 devices as receivers
- One computer for Python signal processing

All three ESP32-C5 devices connect to the same 5 GHz Wi-Fi network. The Wi-Fi channel used in this project is **channel 149**, which belongs to the **5.8 GHz Wi-Fi band**.

The transmitter uses **ESP-NOW broadcast** to send Wi-Fi packets. The two receivers use **ESP-NOW receive mode** and collect CSI data through the **CSI callback function**. After that, both receiver ESP32-C5 devices send CSI data to a computer through **UDP**.

The computer receives CSI data from both receivers and uses Python to process the signal. The processing includes Hampel filtering, Butterworth bandpass filtering, gain amplification, autocorrelation calculation, breathing period estimation, and dynamic receiver selection.

---

## 2. System Structure

```text
                 5 GHz Wi-Fi Router / Access Point
                       Channel 149 / 5.8 GHz
                               |
        ------------------------------------------------
        |                      |                       |
   ESP32-C5 TX           ESP32-C5 RX1            ESP32-C5 RX2
  ESP-NOW Broadcast      ESP-NOW Receive         ESP-NOW Receive
                         CSI Callback            CSI Callback
                              |                       |
                              | UDP CSI Data          | UDP CSI Data
                              |                       |
        ------------------------------------------------
                               |
                            Computer
                    Python Signal Processing
```

---

## 3. Hardware Components

| Component | Quantity | Function |
|---|---:|---|
| ESP32-C5 | 3 | Wi-Fi CSI transmitter and receivers |
| Wi-Fi Router / Access Point | 1 | Provides 5 GHz Wi-Fi connection |
| Computer | 1 | Receives UDP packets and processes CSI data |

---

## 4. ESP32 Device Roles

| Device | Role | Description |
|---|---|---|
| ESP32-C5 TX | Transmitter | Sends ESP-NOW broadcast packets |
| ESP32-C5 RX1 | Receiver | Receives ESP-NOW packets and collects CSI |
| ESP32-C5 RX2 | Receiver | Receives ESP-NOW packets and collects CSI |

---

## 5. Wi-Fi Configuration

All ESP32-C5 devices connect to the same 5 GHz Wi-Fi network.

| Parameter | Value |
|---|---|
| Wi-Fi Band | 5 GHz |
| Wi-Fi Channel | 149 |
| Frequency Band | 5.8 GHz |
| ESP-NOW Mode | Broadcast |
| ESP-NOW Channel | Same as connected Wi-Fi channel |
| Computer Connection | Same Wi-Fi network |

The ESP-NOW working channel must be the same as the connected Wi-Fi channel. In this project, the ESP-NOW channel follows the Wi-Fi channel, which is channel 149.

---

## 6. Communication Architecture

This project uses two communication methods:

1. **ESP-NOW communication between ESP32-C5 devices**
2. **UDP communication from receiver ESP32-C5 devices to the computer**

```text
ESP32-C5 TX
    |
    | ESP-NOW Broadcast
    ↓
ESP32-C5 RX1 ---- UDP ----→ Computer
ESP32-C5 RX2 ---- UDP ----→ Computer
```

---

## 7. ESP-NOW Transmission

The transmitter ESP32-C5 sends ESP-NOW broadcast packets.

Because broadcast mode is used, the transmitter does not need to know the MAC addresses of the receivers.

The ESP-NOW packets act as Wi-Fi signal sources. When the receivers receive these packets, the Wi-Fi driver can generate CSI data.

### Transmitter Workflow

```text
Connect to 5 GHz Wi-Fi
        ↓
Use Wi-Fi channel 149
        ↓
Enable ESP-NOW
        ↓
Send ESP-NOW broadcast packets
        ↓
Receivers receive packets and collect CSI
```

---

## 8. CSI Collection on Receiver ESP32-C5

The two receiver ESP32-C5 devices receive ESP-NOW packets from the transmitter.

When an ESP-NOW packet is received, the CSI callback function is triggered. The callback function extracts the CSI data from the received Wi-Fi packet.

The CSI data reflects changes in the wireless channel. Breathing and small body movements can slightly change the channel, and these changes can appear in the CSI amplitude.

### Receiver Workflow

```text
Connect to the same 5 GHz Wi-Fi network
        ↓
Use Wi-Fi channel 149
        ↓
Enable ESP-NOW receive mode
        ↓
Enable CSI collection
        ↓
Receive ESP-NOW packets
        ↓
CSI callback function is triggered
        ↓
Extract CSI data
        ↓
Send CSI data to computer through UDP
```

---

## 9. UDP Data Transmission

After collecting CSI data, each receiver ESP32-C5 sends the CSI data to the computer through UDP.

The computer is connected to the same Wi-Fi network, so it can receive UDP packets from both receivers.

The Python program identifies the data source using the sender IP address.

```text
ESP32-C5 RX1  ---> UDP ---> Computer
ESP32-C5 RX2  ---> UDP ---> Computer
```

---

## 10. Python Signal Processing Pipeline

The computer runs a Python program to receive and process CSI data in real time.

The full processing pipeline is:

```text
Receive UDP CSI packets
        ↓
Parse CSI data
        ↓
Convert CSI power to amplitude
        ↓
Apply Hampel filter
        ↓
Select useful subcarriers
        ↓
Apply Butterworth bandpass filter
        ↓
Apply signal gain
        ↓
Calculate autocorrelation
        ↓
Estimate breathing period
        ↓
Calculate signal quality scores
        ↓
Dynamically select the best receiver
        ↓
Output final breathing result
```

---

## 11. CSI Data Processing

The receiver ESP32-C5 sends CSI data to the computer.

In Python, the CSI data is parsed and converted into amplitude values.

If the ESP32 sends power data, the amplitude is calculated as:

```text
amplitude = sqrt(power)
```

where:

```text
power = I² + Q²
```

The amplitude data is then used for filtering and breathing detection.

---

## 12. Hampel Filter

The Hampel filter is used to remove abnormal values from the CSI amplitude signal.

CSI data may contain sudden spikes caused by noise, packet instability, or wireless interference. These abnormal points can affect the breathing detection result.

The Hampel filter compares a new data point with the median value of nearby points. If the new point is too far away from the local median, it is treated as an outlier and replaced by the median.

### Hampel Filter Concept

```text
If a data point is very different from nearby points,
replace it with the local median value.
```

### Purpose of Hampel Filtering

- Remove sudden abnormal spikes
- Improve signal stability
- Reduce the effect of noise
- Prepare the signal for bandpass filtering

---

## 13. Butterworth Bandpass Filter

After Hampel filtering, the signal is processed by a Butterworth bandpass filter.

The bandpass frequency range used in this project is:

```text
0.1 Hz - 10 Hz
```

This range is used because breathing and small human movements usually belong to low-frequency signal components.

### The bandpass filter removes:

- Very slow baseline drift
- High-frequency noise
- Unwanted signal components outside the target frequency range

### Filter Configuration

| Parameter | Value |
|---|---:|
| Filter Type | Butterworth Bandpass Filter |
| Low Cutoff Frequency | 0.1 Hz |
| High Cutoff Frequency | 10 Hz |
| Target Signal | Breathing and small-motion signal |

---

## 14. Gain Amplification

After bandpass filtering, the filtered signal is multiplied by a gain value.

The gain is used to make small CSI variations more visible and easier to analyze.

```text
final_signal = bandpassed_signal × gain
```

Gain amplification does not create new signal information. It only increases the visual and numerical scale of the filtered signal.

---

## 15. Autocorrelation for Breathing Period Detection

The processed signal is used for autocorrelation calculation.

Autocorrelation is used to find repeating patterns in a signal. Since breathing is usually periodic, the CSI signal caused by breathing should also contain periodic changes.

Autocorrelation compares the signal with a delayed version of itself.

If the signal becomes similar to itself after a certain delay, that delay can be treated as the breathing period.

### Example

```text
If the signal repeats every 4 seconds,
the autocorrelation result should have a strong peak around 4 seconds.
```

The breathing frequency can be calculated as:

```text
breathing_frequency = 1 / breathing_period
```

Example:

```text
breathing_period = 4 seconds
breathing_frequency = 0.25 Hz
```

---

## 16. Signal Quality Scoring

The system calculates several scores to evaluate the signal quality from each receiver.

| Score | Meaning |
|---|---|
| Autocorrelation score | Measures how periodic the signal is |
| Stability score | Measures whether the detected period is stable |
| Amplitude score | Measures how strong the filtered signal is |

---

## 17. Autocorrelation Score

The autocorrelation score measures how clearly the signal repeats.

A higher autocorrelation score means the signal has a stronger periodic pattern.

This score is important because breathing is a periodic motion.

---

## 18. Stability Score

The stability score measures whether the detected breathing period is stable over time.

For example, the following periods are stable:

```text
4.0 s, 4.1 s, 3.9 s, 4.0 s
```

The following periods are not stable:

```text
2.0 s, 6.0 s, 3.5 s, 8.0 s
```

A higher stability score means the detected breathing period is more reliable.

---

## 19. Amplitude Score

The amplitude score measures how strong the filtered CSI signal is.

If the signal amplitude is too small, it may be difficult to separate breathing-related changes from noise.

A stronger amplitude usually means the receiver is more sensitive to the current human position, body orientation, or breathing movement.

---

## 20. Dynamic Receiver Selection

Because this system uses two receiver ESP32-C5 devices, the Python program dynamically selects the better receiver signal.

The selection priority is:

```text
Priority 1: Periodicity
Priority 2: Period stability
Priority 3: Signal amplitude
```

This means:

1. The system first selects the receiver with the stronger breathing-like periodic signal.
2. If both receivers have similar periodicity, the system selects the receiver with a more stable period.
3. If both periodicity and stability are similar, the system selects the receiver with the larger signal amplitude.

This dynamic selection improves system reliability because different receiver positions may have different sensitivity to breathing motion.

---

## 21. Why Two Receivers Are Used

Wi-Fi CSI is affected by many factors, including:

- Human position
- Body orientation
- Antenna direction
- Multipath propagation
- Receiver placement
- Room environment

One receiver may detect stronger breathing-related changes in one position, while another receiver may perform better in a different position.

Using two receivers allows the system to compare both signals and dynamically choose the better one.

---

## 22. Main Features

- Three ESP32-C5 based Wi-Fi CSI sensing system
- One ESP-NOW broadcast transmitter
- Two ESP-NOW CSI receivers
- 5 GHz Wi-Fi operation
- Channel 149 / 5.8 GHz configuration
- ESP-NOW and Wi-Fi working on the same channel
- CSI collection using CSI callback function
- UDP real-time CSI transmission to computer
- Python real-time signal processing
- Hampel outlier filtering
- 0.1-10 Hz Butterworth bandpass filtering
- Signal gain amplification
- Autocorrelation-based breathing period estimation
- Signal quality scoring
- Dynamic best receiver selection

---

## 23. Complete System Workflow

```text
ESP32-C5 transmitter
        ↓
ESP-NOW broadcast packet
        ↓
ESP32-C5 receiver 1 and receiver 2
        ↓
CSI callback function
        ↓
CSI amplitude extraction
        ↓
UDP transmission
        ↓
Computer Python program
        ↓
Hampel filtering
        ↓
Butterworth bandpass filtering
        ↓
Gain amplification
        ↓
Autocorrelation calculation
        ↓
Breathing period estimation
        ↓
Signal scoring
        ↓
Best receiver selection
        ↓
Final breathing result
```

---

## 24. Applications

This project can be used for:

- Contactless breathing detection
- Wi-Fi based human sensing
- Small motion detection
- CSI signal processing research
- Indoor wireless sensing experiments
- ESP32-C5 CSI development

---

## 25. Notes

- The ESP-NOW channel must match the connected Wi-Fi channel.
- In this project, all ESP32-C5 devices use Wi-Fi channel 149.
- The Wi-Fi network operates in the 5.8 GHz band.
- The computer must connect to the same Wi-Fi network as the ESP32-C5 devices.
- The final breathing result depends on CSI data quality, receiver placement, human position, and the surrounding environment.
- Gain amplification improves signal visibility but does not create new information.
- The dynamic receiver selection only selects the better signal from the two receivers; it does not merge both signals into one signal.

---

## 26. Summary

This project builds a real-time Wi-Fi CSI breathing detection system using three ESP32-C5 devices.

One ESP32-C5 works as an ESP-NOW broadcast transmitter, and two ESP32-C5 devices work as ESP-NOW receivers. All devices connect to the same 5 GHz Wi-Fi network on channel 149. The receivers collect CSI data through the CSI callback function and send the data to a computer through UDP.

The computer uses Python to process the CSI signal. The signal processing includes Hampel filtering, Butterworth bandpass filtering, gain amplification, autocorrelation calculation, breathing period estimation, and signal quality scoring.

Finally, the system dynamically selects the best receiver signal according to periodicity, stability, and amplitude, and uses the selected signal to estimate the breathing result.
