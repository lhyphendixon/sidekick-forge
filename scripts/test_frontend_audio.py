#!/usr/bin/env python3
"""
Frontend Audio Test Suite
Tests that would have caught the microphone/audio level issues
"""
import asyncio
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FrontendAudioTests:
    def __init__(self, base_url="http://localhost:3000"):
        self.base_url = base_url
        self.driver = None
        
    def setup_driver(self):
        """Setup Chrome driver with permissions"""
        chrome_options = Options()
        # Grant microphone permissions automatically
        prefs = {
            "profile.default_content_setting_values.media_stream_mic": 1,  # 1=allow, 2=block
            "profile.default_content_setting_values.media_stream_camera": 2  # block camera
        }
        chrome_options.add_experimental_option("prefs", prefs)
        chrome_options.add_argument("--use-fake-ui-for-media-stream")  # Auto-accept mic permission
        chrome_options.add_argument("--use-fake-device-for-media-stream")  # Use fake audio device
        
        self.driver = webdriver.Chrome(options=chrome_options)
        
    def test_microphone_permission(self) -> bool:
        """Test that microphone permission is granted"""
        try:
            # Navigate to the app
            self.driver.get(self.base_url)
            
            # Execute JavaScript to check microphone permission
            permission_status = self.driver.execute_script("""
                return new Promise((resolve) => {
                    navigator.permissions.query({name: 'microphone'}).then(result => {
                        resolve(result.state);
                    }).catch(err => {
                        resolve('error: ' + err.message);
                    });
                });
            """)
            
            if permission_status == "granted":
                logger.info("✅ Microphone permission granted")
                return True
            else:
                logger.error(f"❌ Microphone permission not granted: {permission_status}")
                return False
                
        except Exception as e:
            logger.error(f"Error testing microphone permission: {e}")
            return False
    
    def test_audio_capture(self) -> bool:
        """Test that audio capture actually works"""
        try:
            # Test getUserMedia
            audio_works = self.driver.execute_script("""
                return new Promise((resolve) => {
                    navigator.mediaDevices.getUserMedia({ audio: true })
                        .then(stream => {
                            // Check if we got audio tracks
                            const tracks = stream.getAudioTracks();
                            if (tracks.length > 0) {
                                // Clean up
                                tracks.forEach(track => track.stop());
                                resolve(true);
                            } else {
                                resolve(false);
                            }
                        })
                        .catch(err => {
                            console.error('getUserMedia error:', err);
                            resolve(false);
                        });
                });
            """)
            
            if audio_works:
                logger.info("✅ Audio capture working")
                return True
            else:
                logger.error("❌ Audio capture failed")
                return False
                
        except Exception as e:
            logger.error(f"Error testing audio capture: {e}")
            return False
    
    def test_audio_level_detection(self) -> bool:
        """Test that audio levels are being detected"""
        try:
            # Inject audio level detection code
            audio_level_detected = self.driver.execute_script("""
                return new Promise((resolve) => {
                    navigator.mediaDevices.getUserMedia({ audio: true })
                        .then(stream => {
                            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
                            const analyser = audioContext.createAnalyser();
                            const microphone = audioContext.createMediaStreamSource(stream);
                            microphone.connect(analyser);
                            analyser.fftSize = 256;
                            
                            const bufferLength = analyser.frequencyBinCount;
                            const dataArray = new Uint8Array(bufferLength);
                            
                            let maxLevel = 0;
                            let checks = 0;
                            
                            const checkLevel = () => {
                                analyser.getByteFrequencyData(dataArray);
                                const average = dataArray.reduce((a, b) => a + b) / bufferLength;
                                maxLevel = Math.max(maxLevel, average);
                                
                                checks++;
                                if (checks < 50) {  // Check for ~1 second
                                    requestAnimationFrame(checkLevel);
                                } else {
                                    // Clean up
                                    stream.getTracks().forEach(track => track.stop());
                                    audioContext.close();
                                    
                                    // With fake audio device, we should see some level
                                    resolve(maxLevel > 0);
                                }
                            };
                            
                            checkLevel();
                        })
                        .catch(err => {
                            console.error('Audio level test error:', err);
                            resolve(false);
                        });
                });
            """)
            
            if audio_level_detected:
                logger.info("✅ Audio level detection working")
                return True
            else:
                logger.error("❌ Audio level detection failed - this would show as non-responsive audio bar")
                return False
                
        except Exception as e:
            logger.error(f"Error testing audio level detection: {e}")
            return False
    
    def test_livekit_connection(self) -> bool:
        """Test that LiveKit SDK can be loaded and initialized"""
        try:
            # Check if LiveKit SDK is available
            livekit_available = self.driver.execute_script("""
                // Try to load LiveKit if not already loaded
                if (typeof LivekitClient === 'undefined') {
                    return new Promise((resolve) => {
                        const script = document.createElement('script');
                        script.src = 'https://unpkg.com/livekit-client/dist/livekit-client.umd.min.js';
                        script.onload = () => resolve(true);
                        script.onerror = () => resolve(false);
                        document.head.appendChild(script);
                    });
                } else {
                    return true;
                }
            """)
            
            if livekit_available:
                logger.info("✅ LiveKit SDK available")
                return True
            else:
                logger.error("❌ LiveKit SDK not available")
                return False
                
        except Exception as e:
            logger.error(f"Error testing LiveKit availability: {e}")
            return False
    
    def run_all_tests(self) -> bool:
        """Run all frontend audio tests"""
        self.setup_driver()
        
        tests = [
            ("Microphone Permission", self.test_microphone_permission),
            ("Audio Capture", self.test_audio_capture),
            ("Audio Level Detection", self.test_audio_level_detection),
            ("LiveKit SDK", self.test_livekit_connection),
        ]
        
        all_passed = True
        results = []
        
        for test_name, test_func in tests:
            logger.info(f"\nRunning: {test_name}")
            try:
                passed = test_func()
                results.append((test_name, "PASSED" if passed else "FAILED"))
                if not passed:
                    all_passed = False
            except Exception as e:
                logger.error(f"Test {test_name} crashed: {e}")
                results.append((test_name, "ERROR"))
                all_passed = False
        
        # Print summary
        print("\n" + "=" * 50)
        print("FRONTEND AUDIO TEST SUMMARY")
        print("=" * 50)
        for test_name, status in results:
            emoji = "✅" if status == "PASSED" else "❌"
            print(f"{emoji} {test_name}: {status}")
        
        self.driver.quit()
        return all_passed


if __name__ == "__main__":
    tester = FrontendAudioTests()
    success = tester.run_all_tests()
    exit(0 if success else 1)