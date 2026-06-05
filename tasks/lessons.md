# Lessons Learned

## Mock Mode vs. Real Integrations
* **Issue:** Stating that a notification (e.g., a WhatsApp message) was "triggered/delivered successfully" when mock mode (`MOCK_PROVIDERS=True`) was active. Although the system correctly generated database records with status `"sent"` and printed mock output to standard output, it did not dispatch any actual messages to the physical device.
* **Rule:** Always differentiate clearly between mock simulation delivery and real integration dispatch. If a user reports that a message did not arrive on their device, first verify if `MOCK_PROVIDERS` is enabled, and explain that in mock mode, delivery is simulated locally in logs and DB records rather than sent to external APIs (like Twilio).
