#!/usr/bin/env python3
"""Test to demonstrate the profile name issue"""

# Simulate the profile data from the logs
profile_data = {
    "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
    "email": "l-dixon@autonomite.net",
    "full_name": "leandrew",  # This field exists!
    # Note: no "name" field
}

# Test the context formatting logic from context.py
def format_profile_section(profile):
    sections = []
    
    # This is the logic from context.py line 721-724
    name = profile.get("name") or profile.get("full_name") or profile.get("display_name") or profile.get("username")
    if name:
        sections.append(f"**Name:** {name}  ")
    
    if profile.get("email"):
        sections.append(f"**Email:** {profile['email']}  ")
    
    return "\n".join(sections)

# Test 1: Current code should work
print("Test 1: Profile formatting with full_name")
print(format_profile_section(profile_data))
print()

# Test 2: What if full_name was None or empty?
profile_data_empty_name = profile_data.copy()
profile_data_empty_name["full_name"] = None
print("Test 2: Profile with None full_name")
print(format_profile_section(profile_data_empty_name))
print()

# Test 3: Check what the agent is actually seeing
print("Test 3: Raw profile data check")
print(f"Profile has full_name field: {'full_name' in profile_data}")
print(f"full_name value: {repr(profile_data.get('full_name'))}")
print(f"full_name is truthy: {bool(profile_data.get('full_name'))}")