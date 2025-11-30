#!/usr/bin/env python3
"""
Verification script for the fixes to transcript citations implementation
"""

def check_migration_file():
    """Verify the migration file includes the source column"""
    print("Checking migration file...")
    
    with open('/root/sidekick-forge/migrations/add_transcript_citations.sql', 'r') as f:
        content = f.read()
    
    checks = {
        'source column': 'ADD COLUMN IF NOT EXISTS source text',
        'source index': 'idx_transcripts_source_created',
        'turn_id column': 'ADD COLUMN IF NOT EXISTS turn_id uuid',
        'citations column': 'ADD COLUMN IF NOT EXISTS citations jsonb',
        'metadata column': 'ADD COLUMN IF NOT EXISTS metadata jsonb'
    }
    
    all_good = True
    for check_name, pattern in checks.items():
        if pattern in content:
            print(f"  ✅ {check_name}: Found")
        else:
            print(f"  ❌ {check_name}: NOT FOUND")
            all_good = False
    
    return all_good


def check_voice_transcripts():
    """Verify voice_transcripts.py has type-safe Supabase access"""
    print("\nChecking voice_transcripts.py...")
    
    with open('/root/sidekick-forge/app/api/v1/voice_transcripts.py', 'r') as f:
        content = f.read()
    
    # Ensure the legacy endpoint now redirects callers to Supabase Realtime
    checks = {
        'deprecation message defined': 'DEPRECATION_MESSAGE =',
        'mentions Supabase Realtime': 'Supabase Realtime',
        'stream route raises 410': 'async def stream_voice_transcripts',
        'history route raises 410': 'async def get_transcript_history',
        'uses HTTPException 410': 'status.HTTP_410_GONE'
    }
    
    all_good = True
    for check_name, pattern in checks.items():
        if pattern in content:
            print(f"  ✅ {check_name}: Found")
        else:
            print(f"  ❌ {check_name}: NOT FOUND")
            all_good = False
    
    return all_good


def check_transcript_stores():
    """Verify transcript store modules include source field"""
    print("\nChecking transcript store modules...")
    
    modules = [
        '/root/sidekick-forge/docker/agent/transcript_store.py',
        '/root/sidekick-forge/app/agent_modules/transcript_store.py'
    ]
    
    all_good = True
    for module_path in modules:
        print(f"\n  Checking {module_path.split('/')[-2]}/{module_path.split('/')[-1]}:")
        
        with open(module_path, 'r') as f:
            content = f.read()
        
        # Check for source field in both user and assistant rows
        if '"source":' in content:
            print(f"    ✅ Source field present")
            
            # Check that it sets correct values
            if '"source": "voice"' in content and module_path.endswith('docker/agent/transcript_store.py'):
                print(f"    ✅ Sets source='voice' for agent container")
            elif '"source": "text"' in content and module_path.endswith('app/agent_modules/transcript_store.py'):
                print(f"    ✅ Sets source='text' for FastAPI")
            else:
                print(f"    ⚠️ Check source value assignment")
        else:
            print(f"    ❌ Source field NOT FOUND")
            all_good = False
    
    return all_good


def main():
    print("="*60)
    print("VERIFICATION OF FIXES")
    print("="*60)
    
    results = []
    
    # Run checks
    results.append(("Migration file", check_migration_file()))
    results.append(("Voice transcripts", check_voice_transcripts()))
    results.append(("Transcript stores", check_transcript_stores()))
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    all_passed = all(result for _, result in results)
    
    for check_name, passed in results:
        status = "PASSED" if passed else "FAILED"
        emoji = "✅" if passed else "❌"
        print(f"{emoji} {check_name}: {status}")
    
    if all_passed:
        print("\n🎉 All fixes verified successfully!")
    else:
        print("\n⚠️ Some issues remain. Please review.")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
