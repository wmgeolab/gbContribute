from datetime import datetime

LOGBASEPATH = "/home/rohith/work/gbcontributebackend/logfile.log"


def pLogger(type, message, path=LOGBASEPATH):
    with open(path, "a") as f:
        f.write(str(datetime.now().strftime('%Y-%m-%d %H:%M:%S')) + ": (" + str(type) + ") " + str(message) + "\n")

pLogger("INFO", "Fetching driving network data from PBF.")