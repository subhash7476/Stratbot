import os
import signal
import subprocess

def stop():
    # Very crude way to kill on Windows without psutil
    try:
        # Just use taskkill to be sure
        subprocess.run(["taskkill", "/F", "/IM", "python.exe", "/FI", "WINDOWTITLE eq MockPublisher"], capture_output=True)
        # Or more specifically if we started it via & in this shell, it might be harder.
        # Let's just try to find processes running mock_zmq_publisher.py
        output = subprocess.check_output(['wmic', 'process', 'where', "commandline like '%mock_zmq_publisher.py%'", 'get', 'processid'], shell=True).decode()
        pids = [line.strip() for line in output.split('\n') if line.strip() and line.strip().isdigit()]
        for pid in pids:
            print(f"Killing PID {pid}")
            os.kill(int(pid), signal.SIGTERM)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    stop()
