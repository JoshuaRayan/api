import datetime
from typing import List, Tuple
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import os
import pickle
import pytz
import json
from dotenv import load_dotenv
import config

load_dotenv()

# Constants
SCOPES = ['https://www.googleapis.com/auth/calendar']
IST = pytz.timezone('Asia/Kolkata')
WORK_START_HOUR = 9
WORK_END_HOUR = 19    

# --- API Keys and Clients ---
DEEPGRAM_API_KEY = config.DEEPGRAM_API_KEY  # (Unused, but left for config completeness)

class GoogleCalendarService:
    def __init__(self):
        self.service = self._authenticate()

    def _authenticate(self):
        creds = None
        if os.path.exists('token.json'):
            try:
                with open('token.json', 'rb') as token:
                    creds = pickle.load(token)
            except pickle.UnpicklingError:
                print("Warning: token.json is corrupted. Deleting and re-authenticating.")
                os.remove('token.json')
                creds = None # Force re-authentication

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                # Use a fixed port and explicitly set the redirect URI
                creds = flow.run_local_server(
                    port=5000,
                    authorization_prompt_message='Please visit this URL: {url}',
                    success_message='The auth flow is complete; you may close this window.',
                    open_browser=True
                )

            with open('token.json', 'wb') as token:
                pickle.dump(creds, token)

        return build('calendar', 'v3', credentials=creds)

    def get_busy_events_for_day(self, start_time: datetime.datetime, end_time: datetime.datetime) -> List[Tuple[datetime.datetime, datetime.datetime]]:
        time_min = start_time.astimezone(pytz.UTC).isoformat()
        time_max = end_time.astimezone(pytz.UTC).isoformat()

        events_result = self.service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        
        busy_slots = []
        for event in events:
            start = event['start'].get('dateTime')
            end = event['end'].get('dateTime')

            if start and end:
                start_dt_utc = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                end_dt_utc = datetime.datetime.fromisoformat(end.replace('Z', '+00:00'))

                busy_slots.append((
                    start_dt_utc.astimezone(IST),
                    end_dt_utc.astimezone(IST)
                ))
        return busy_slots

    def book_meeting(self, start_time: datetime.datetime, end_time: datetime.datetime, summary: str = "Appointment", phone_number: str = None) -> str:
        event = {
            'summary': summary,
            'start': {
                'dateTime': start_time.astimezone(pytz.UTC).isoformat(),
                'timeZone': 'Asia/Kolkata'
            },
            'end': {
                'dateTime': end_time.astimezone(pytz.UTC).isoformat(),
                'timeZone': 'Asia/Kolkata'
            },
            'reminders': {
                'useDefault': True,
            }
        }
        if phone_number:
            event['description'] = f"Phone Number: {phone_number}"

        created_event = self.service.events().insert(calendarId='primary', body=event).execute()
        return created_event.get('htmlLink', '')

    def get_events_by_phone_number(self, phone_number: str) -> List[dict]:
        # Search for events in a reasonable time range (e.g., 1 year in the past, 1 year in the future)
        now = datetime.datetime.now(pytz.UTC)
        time_min = (now - datetime.timedelta(days=365)).isoformat()
        time_max = (now + datetime.timedelta(days=365)).isoformat()

        events_result = self.service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])

        matching_events = []
        for event in events:
            description = event.get('description', '')
            if f"Phone Number: {phone_number}" in description:
                matching_events.append(event)
        return matching_events

_calendar_service_instance = None

def get_calendar_service_instance():
    global _calendar_service_instance
    if _calendar_service_instance is None:
        _calendar_service_instance = GoogleCalendarService()
    return _calendar_service_instance

def get_busy_events_for_day(start_time: datetime.datetime, end_time: datetime.datetime) -> List[Tuple[datetime.datetime, datetime.datetime]]:
    service_instance = get_calendar_service_instance()
    return service_instance.get_busy_events_for_day(start_time, end_time)

def find_free_slots(start_time: datetime.datetime, end_time: datetime.datetime, duration_minutes: int = 60) -> List[Tuple[datetime.datetime, datetime.datetime]]:
    """
    Find all available time slots for a meeting of the specified duration within a given time range.
    
    Args:
        start_time: Start of the time range (datetime object in IST).
        end_time: End of the time range (datetime object in IST).
        duration_minutes: Duration of the meeting in minutes.
        
    Returns:
        List of (start_time, end_time) tuples in the local timezone (IST)
    """
    busy_slots = get_busy_events_for_day(start_time, end_time)
    busy_slots.sort()
    
    # Calculate slot duration
    slot_duration = datetime.timedelta(minutes=duration_minutes)
    
    # Filter out slots that overlap with busy times
    free_slots = []
    current_time = start_time

    for busy_start, busy_end in busy_slots:
        # Add free slots before the current busy slot, within the requested range
        while current_time + slot_duration <= busy_start and current_time + slot_duration <= end_time:
            free_slots.append((current_time, current_time + slot_duration))
            current_time += slot_duration # Move to next interval by slot_duration
        
        # Move current_time past the busy slot
        current_time = max(current_time, busy_end)

    # Add free slots after the last busy slot until the end of the requested range
    while current_time + slot_duration <= end_time:
        free_slots.append((current_time, current_time + slot_duration))
        current_time += slot_duration # Move to next interval by slot_duration
    
    # Remove duplicates and sort - this step is still necessary if intervals overlap or due to logic
    free_slots = sorted(list(set(free_slots)))
    
    return free_slots

def format_slots(slots: List[Tuple[datetime.datetime, datetime.datetime]]) -> List[str]:
    return [f"{start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}" for start, end in slots]

def book_meeting(start_time: datetime.datetime, end_time: datetime.datetime, summary: str = "Appointment", phone_number: str = None) -> str:
    service_instance = get_calendar_service_instance()
    return service_instance.book_meeting(start_time, end_time, summary, phone_number)

def update_appointment(event_id: str, new_start_time: datetime.datetime, new_end_time: datetime.datetime, new_summary: str = "Appointment", phone_number: str = None) -> str:
    service_instance = get_calendar_service_instance()
    updated_event_body = {
        'summary': new_summary,
        'start': {
            'dateTime': new_start_time.astimezone(pytz.UTC).isoformat(),
            'timeZone': 'Asia/Kolkata'
        },
        'end': {
            'dateTime': new_end_time.astimezone(pytz.UTC).isoformat(),
            'timeZone': 'Asia/Kolkata'
        },
        'reminders': {
            'useDefault': True,
        }
    }
    if phone_number:
        updated_event_body['description'] = f"Phone Number: {phone_number}"
    try:
        service_instance.service.events().update(
            calendarId='primary',
            eventId=event_id,
            body=updated_event_body
        ).execute()
        return "Appointment updated successfully."
    except Exception as e:
        return f"Error updating appointment: {e}"

def delete_appointment(phone_number: str, summary: str = None) -> str:
    service_instance = get_calendar_service_instance()
    matching_events = service_instance.get_events_by_phone_number(phone_number)
    if not matching_events:
        return "No appointments found for the given phone number."
    if summary:
        lower_summary = summary.lower()
        filtered_events = [event for event in matching_events if lower_summary in event.get('summary', '').lower()]
    else:
        filtered_events = matching_events
    if not filtered_events:
        return "No appointments found with that phone number and matching title."
    deleted_count = 0
    for event in filtered_events:
        event_id = event['id']
        try:
            service_instance.service.events().delete(
                calendarId='primary',
                eventId=event_id
            ).execute()
            deleted_count += 1
        except Exception as e:
            print(f"Error deleting event {event_id}: {e}")
    return f"Successfully deleted {deleted_count} appointment(s)."