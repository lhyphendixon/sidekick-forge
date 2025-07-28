#!/usr/bin/env python3
"""
Pre-Deployment Checklist
Run this before any deployment to catch configuration issues
"""
import asyncio
import json
import subprocess
import sys
import httpx
import logging
from typing import Dict, List, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PreDeploymentChecklist:
    def __init__(self):
        self.checks = []
        self.failures = []
        
    async def run_checklist(self) -> bool:
        """Run all pre-deployment checks"""
        logger.info("📋 PRE-DEPLOYMENT CHECKLIST")
        logger.info("=" * 50)
        
        checks = [
            ("Environment Variables", self.check_environment_variables),
            ("Docker Services", self.check_docker_services),
            ("Database Connectivity", self.check_database_connectivity),
            ("API Keys Validity", self.check_api_keys),
            ("Model Availability", self.check_model_availability),
            ("Agent Configuration", self.check_agent_configuration),
            ("LiveKit Connectivity", self.check_livekit),
            ("Mission Critical Tests", self.run_mission_critical_tests),
        ]
        
        for check_name, check_func in checks:
            logger.info(f"\n🔍 Checking: {check_name}")
            try:
                result = await check_func()
                self.checks.append((check_name, result))
                if not result:
                    self.failures.append(check_name)
            except Exception as e:
                logger.error(f"❌ {check_name} failed with exception: {e}")
                self.checks.append((check_name, False))
                self.failures.append(check_name)
        
        self._print_summary()
        return len(self.failures) == 0
    
    async def check_environment_variables(self) -> bool:
        """Check critical environment variables"""
        required_vars = [
            "SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY",
            "LIVEKIT_URL",
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
        ]
        
        import os
        from dotenv import load_dotenv
        load_dotenv('/root/sidekick-forge/.env')
        
        missing = []
        for var in required_vars:
            value = os.getenv(var)
            if not value:
                missing.append(var)
                logger.error(f"❌ Missing: {var}")
            else:
                # Check for known bad values
                if var == "SUPABASE_URL" and "yuowazxcxwhczywurmmw" in value:
                    logger.error(f"❌ {var} has wrong Supabase instance!")
                    logger.error(f"   Should be: eukudpgfpihxsypulopm")
                    missing.append(var)
                elif var == "LIVEKIT_API_KEY" and value == "APIUtuiQ47BQBsk":
                    logger.error(f"❌ {var} has expired credentials!")
                    missing.append(var)
                else:
                    logger.info(f"✅ {var} is set")
        
        return len(missing) == 0
    
    async def check_docker_services(self) -> bool:
        """Check that all required Docker services are running"""
        required_services = [
            "fastapi",
            "agent-worker",
            "redis",
            "nginx",
        ]
        
        try:
            result = subprocess.run(
                ["docker-compose", "ps"],
                capture_output=True,
                text=True,
                cwd="/root/sidekick-forge"
            )
            
            output = result.stdout
            missing_services = []
            
            for service in required_services:
                if service in output and "Up" in output:
                    logger.info(f"✅ {service} is running")
                else:
                    logger.error(f"❌ {service} is not running")
                    missing_services.append(service)
            
            return len(missing_services) == 0
            
        except Exception as e:
            logger.error(f"Error checking Docker services: {e}")
            return False
    
    async def check_database_connectivity(self) -> bool:
        """Check Supabase connectivity"""
        import os
        from supabase import create_client
        
        try:
            supabase_url = os.getenv('SUPABASE_URL')
            supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
            
            if not supabase_url or not supabase_key:
                logger.error("❌ Supabase credentials missing")
                return False
            
            # Create client and test connection
            supabase = create_client(supabase_url, supabase_key)
            
            # Try to query clients table
            result = supabase.table('clients').select('id').limit(1).execute()
            
            logger.info("✅ Supabase connection successful")
            return True
            
        except Exception as e:
            logger.error(f"❌ Supabase connection failed: {e}")
            if "Invalid API key" in str(e):
                logger.error("   Service role key is invalid or expired")
            return False
    
    async def check_api_keys(self) -> bool:
        """Check that API keys in database are valid"""
        # Get a test client's API keys
        import os
        from supabase import create_client
        
        try:
            supabase_url = os.getenv('SUPABASE_URL')
            supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
            
            supabase = create_client(supabase_url, supabase_key)
            
            # Get test client
            result = supabase.table('clients').select('settings').eq('id', 'df91fd06-816f-4273-a903-5a4861277040').single().execute()
            
            if not result.data:
                logger.error("❌ Test client not found")
                return False
            
            settings = result.data.get('settings', {})
            if isinstance(settings, str):
                settings = json.loads(settings)
            
            api_keys = settings.get('api_keys', {})
            
            # Check critical keys
            critical_keys = ['groq_api_key', 'deepgram_api_key', 'elevenlabs_api_key']
            missing_keys = []
            
            for key in critical_keys:
                if key in api_keys and api_keys[key] and not api_keys[key].startswith('test'):
                    logger.info(f"✅ {key} is configured")
                else:
                    logger.error(f"❌ {key} is missing or invalid")
                    missing_keys.append(key)
            
            return len(missing_keys) == 0
            
        except Exception as e:
            logger.error(f"Error checking API keys: {e}")
            return False
    
    async def check_model_availability(self) -> bool:
        """Check that configured LLM models are available"""
        # This is CRITICAL - would have caught the Groq model issue
        models_to_check = [
            ("groq", "llama-3.3-70b-versatile", True),  # Should work
            ("groq", "llama-3.1-70b-versatile", False),  # Should fail (deprecated)
            ("groq", "llama3-70b-8192", False),  # Old model name
        ]
        
        all_correct = True
        
        for provider, model, should_work in models_to_check:
            result = await self._test_model(provider, model)
            
            if should_work and result:
                logger.info(f"✅ {provider} model {model} is available")
            elif not should_work and not result:
                logger.info(f"✅ {provider} model {model} correctly identified as unavailable")
            else:
                logger.error(f"❌ {provider} model {model} - expected {'available' if should_work else 'unavailable'} but got opposite")
                all_correct = False
        
        return all_correct
    
    async def _test_model(self, provider: str, model: str) -> bool:
        """Test if a specific model is available"""
        if provider == "groq":
            api_key = "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"  # From test client
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": model,
                "messages": [{"role": "user", "content": "Test"}],
                "max_tokens": 5
            }
            
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers=headers,
                        json=data,
                        timeout=10.0
                    )
                    return response.status_code == 200
                except:
                    return False
        
        return False
    
    async def check_agent_configuration(self) -> bool:
        """Check agent configuration for common issues"""
        # Check entrypoint.py for model mapping
        try:
            with open('/root/sidekick-forge/docker/agent/entrypoint.py', 'r') as f:
                content = f.read()
            
            checks = [
                # Check for model mapping code
                ('Model mapping exists', 'if model == "llama3-70b-8192"' in content),
                # Check for correct model lookup
                ('Looks in voice_settings for model', 'voice_settings.get("llm_model"' in content),
                # Check for new default model
                ('Uses new default model', 'llama-3.3-70b-versatile' in content),
            ]
            
            all_good = True
            for check_name, check_result in checks:
                if check_result:
                    logger.info(f"✅ {check_name}")
                else:
                    logger.error(f"❌ {check_name}")
                    all_good = False
            
            return all_good
            
        except Exception as e:
            logger.error(f"Error checking agent configuration: {e}")
            return False
    
    async def check_livekit(self) -> bool:
        """Check LiveKit connectivity"""
        import os
        
        url = os.getenv("LIVEKIT_URL")
        api_key = os.getenv("LIVEKIT_API_KEY")
        api_secret = os.getenv("LIVEKIT_API_SECRET")
        
        if not all([url, api_key, api_secret]):
            logger.error("❌ LiveKit credentials missing")
            return False
        
        # Check if credentials are expired
        if api_key == "APIUtuiQ47BQBsk":
            logger.error("❌ LiveKit API key is expired!")
            return False
        
        logger.info("✅ LiveKit credentials configured")
        return True
    
    async def run_mission_critical_tests(self) -> bool:
        """Run the mission critical test suite"""
        try:
            result = subprocess.run(
                ["python3", "/root/sidekick-forge/scripts/test_mission_critical.py", "--quick"],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                logger.info("✅ Mission critical tests passed")
                return True
            else:
                logger.error("❌ Mission critical tests failed")
                logger.error(result.stdout[-500:])  # Last 500 chars of output
                return False
                
        except Exception as e:
            logger.error(f"Error running mission critical tests: {e}")
            return False
    
    def _print_summary(self):
        """Print deployment readiness summary"""
        print("\n" + "=" * 60)
        print("DEPLOYMENT READINESS SUMMARY")
        print("=" * 60)
        
        for check_name, passed in self.checks:
            emoji = "✅" if passed else "❌"
            print(f"{emoji} {check_name}: {'PASSED' if passed else 'FAILED'}")
        
        print("\n" + "=" * 60)
        
        if self.failures:
            print("\n🚨 DEPLOYMENT BLOCKED - Fix these issues:")
            for failure in self.failures:
                print(f"  - {failure}")
            print("\n⚠️  DO NOT DEPLOY until all checks pass!")
        else:
            print("\n✅ All checks passed! Ready to deploy.")
            print("\n🚀 Recommended: Run comprehensive tests before final deployment")


async def main():
    checklist = PreDeploymentChecklist()
    success = await checklist.run_checklist()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())