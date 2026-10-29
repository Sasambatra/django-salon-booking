from booking_rules import fits_in_gap

service = {
    "name": "Haircut appointment",
    "price_rub": 300,
    "duration_minutes": 30,
}

print(service["name"])
print("Price:", service["price_rub"], "RUB")
print("Duration:", service["duration_minutes"], "minutes")

print("20-minute gap:", fits_in_gap(service["duration_minutes"], 20))
print("30-minute gap:", fits_in_gap(service["duration_minutes"], 30))
print("45-minute gap:", fits_in_gap(service["duration_minutes"], 45))