from flask import Flask
import requests
import datetime
import re
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

EVENTS_URLS = {
    "SMSP": "https://www.smsprd.com/json/events/events/getEventsForSelect?filters%5Bpublic%5D=true&filters%5BexcludeId%5D=101293",
    "PI": "https://www.phillipislandridedays.com.au/json/events/events/getEventsForSelect?filters%5Bpublic%5D=true&filters%5BexcludeId%5D=101276"
}

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1375055572176474132/jrJNbxZsV_1UpW4PfASJ_HF7LkkuzJe7k6bA_HVBB_ezb63vZpbE3nIcYCvwzZ_Gm7ut"
DISCORD_WEBHOOK_PHEASANT_WOOD = DISCORD_WEBHOOK_URL

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Cookie": "PHPSESSID=9u0u0g8f1suparah2si7uaqm35; selmasuPublicToken=IH0OJCUtfK25ad3Dtb2uCHeyjTvXgMOA; _ga=GA1.3.1658598946.1747809112; _gid=GA1.3.1158673676.1747809112; _fbp=fb.2.1747821472993.219493874915532452; _ga_CDKJPXNRQF=GS2.3.s1747883817$o3$g1$t1747883833$j0$l0$h0; selmasuToken=hgSQb9rWmc7wcShhDBAT; _gat=1"
}

PW_API_URL = "https://94qrm2we1l.execute-api.us-east-1.amazonaws.com/production/storefront/calendar"
PW_SHOP = "pheasant-wood.myshopify.com"

def extract_date_from_name(name):
    match = re.search(r'(\d{1,2})(?:st|nd|rd|th)? (\w+) (\d{4})', name)
    if not match:
        return None
    day, month_str, year = match.groups()
    month_map = {
        'january':1, 'february':2, 'march':3, 'april':4,
        'may':5, 'june':6, 'july':7, 'august':8,
        'september':9, 'october':10, 'november':11, 'december':12
    }
    return datetime.date(int(year), month_map.get(month_str.lower(), 0), int(day))

def get_events(url):
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()

def format_sms_pi_date(date_str):
    date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    day_suffix = lambda d: 'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')
    day = date.day
    suffix = day_suffix(day)
    return date.strftime(f"%a {day}{suffix} %B %Y")

def fetch_pheasant_wood_events():
    today = datetime.date.today()
    start_date = today.strftime("%Y-%m-%d")
    end_date = (today + datetime.timedelta(weeks=8)).strftime("%Y-%m-%d")
    params = {
        "shop": PW_SHOP,
        "startDate": start_date,
        "endDate": end_date,
        "currentDate": start_date
    }
    response = requests.get(PW_API_URL, params=params)
    response.raise_for_status()
    data = response.json()
    events = data.get("events", [])
    filtered = []
    keywords = ["social ride day", "125cc", "150cc"]
    for event in events:
        title = event.get("title", "").lower()
        if any(kw in title for kw in keywords):
            start_at = event.get("start_at") or event.get("start")
            if not start_at:
                continue
            try:
                event_date = datetime.datetime.fromisoformat(start_at.replace("Z", "+00:00")).date()
            except Exception:
                continue
            if event_date >= today:
                tickets_available = 0
                for ticket in event.get("ticket_types", []):
                    inventory = ticket.get("inventory")
                    if isinstance(inventory, int):
                        tickets_available += inventory
                filtered.append((event_date.strftime("%Y-%m-%d"), event.get("title", "No title"), tickets_available))
    filtered.sort()
    return filtered

def format_pheasant_wood_message():
    events = fetch_pheasant_wood_events()
    if not events:
        return "❌ No matching upcoming Pheasant Wood events found."

    groups = {
        "Ride days": [],
        "125cc Enduro": [],
        "150cc Enduro": []
    }

    for date, title, inventory in events:
        lower_title = title.lower()
        if "social ride day" in lower_title:
            groups["Ride days"].append((date, inventory))
        elif "125cc" in lower_title:
            groups["125cc Enduro"].append((date, inventory))
        elif "150cc" in lower_title:
            groups["150cc Enduro"].append((date, inventory))

    msg = "\n**PW**"
    for group_name, group_events in groups.items():
        if not group_events:
            continue
        msg += f"\n**{group_name}**\n"
        for date, inventory in sorted(group_events):
            date_str = format_sms_pi_date(date)
            msg += f"{date_str} (Remaining: {inventory})\n"

    return msg

def format_message():
    today = datetime.date.today()
    grouped_events = {}

    for location, url in EVENTS_URLS.items():
        events = get_events(url)
        upcoming = []
        for e in events:
            name = e.get("name", "")
            date = extract_date_from_name(name)
            tickets = e.get("totalAvailable", "N/A")
            if date and date >= today:
                upcoming.append((date, name, tickets))
        upcoming.sort()
        grouped_events[location] = upcoming[:5]

    msg = "**📅 Upcoming Events:**\n"
    for location in ["SMSP", "PI"]:
        events = grouped_events.get(location, [])
        msg += f"\n**{location}**\n"
        for date, name, tickets in events:
            msg += f"{date.strftime('%a %d %B %Y')} (Remaining: {tickets})\n"

    msg += format_pheasant_wood_message()
    return msg

def send_to_discord(webhook_url, message):
    if not message or len(message.strip()) == 0:
        message = "No events available at this time."

    if len(message) > 1900:
        message = message[:1900] + "...\n*Message truncated.*"

    response = requests.post(webhook_url, json={"content": message})
    response.raise_for_status()

def post_events_to_discord():
    try:
        message = format_message()
        send_to_discord(DISCORD_WEBHOOK_URL, message)
        print("✅ SMSP, PI & Pheasant Wood events message posted to Discord!")
    except Exception as e:
        print(f"❌ Error posting combined events to Discord: {e}")

scheduler = BackgroundScheduler(timezone="Australia/Sydney")
scheduler.add_job(post_events_to_discord, 'cron', hour=8, minute=0)
scheduler.start()

@app.route("/")
def trigger():
    post_events_to_discord()
    return "✅ Event summary posted to Discord."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
