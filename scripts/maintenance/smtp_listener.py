import aiosmtpd.controller
import logging
import os
import email
from email.policy import default

class MockHandler:
    async def handle_DATA(self, server, session, envelope):
        data = envelope.content.decode('utf-8', errors='replace')
        msg = email.message_from_string(data, policy=default)
        
        print(f"Message received from: {envelope.mail_from}")
        print(f"Message for: {envelope.rcpt_tos}")
        
        decoded_content = ""
        if msg.is_multipart():
            for part in msg.iter_parts():
                payload = part.get_payload(decode=True)
                if payload:
                    decoded_content += payload.decode('utf-8', errors='replace')
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                decoded_content = payload.decode('utf-8', errors='replace')

        # Append to a log file for test scripts to read
        log_path = "logs/smtp.log"
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write("--- EMAIL ---\n")
            f.write(f"To: {envelope.rcpt_tos}\n")
            f.write(f"Subject: {msg['Subject']}\n")
            f.write(f"Decoded-Body: {decoded_content}\n")
            f.write("--- END ---\n")
            f.flush()
            
        return '250 OK'

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    handler = MockHandler()
    controller = aiosmtpd.controller.Controller(handler, hostname='localhost', port=1025)
    print("Starting mock SMTP server on localhost:1025...")
    controller.start()
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        controller.stop()
