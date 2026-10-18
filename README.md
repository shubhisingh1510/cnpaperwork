# cnpaperwork

Paper, data and test kit for **"Threat-Modelling WebRTC-Based IP and Metadata Leakage on University BYOD Networks: A Campus-Specific Countermeasure Framework with an Emulated Evaluation"** by Shubhi Singh and Jilagam Yatin (VIT Vellore).

| Folder / file | What it is |
|---|---|
| `cnfinalpaper.pdf` | The paper (IEEE conference format, 10 pages) |
| `paper_source/` | LaTeX source: `.tex`, `references.bib`, generated table bodies |
| `experiment/` | Emulated testbed (`run_matrix.py`, `agent.py`, `analyze.py`) and its raw and processed results (560 + 140 sessions) |
| `hardware_test/server.py` | Test page for real phones and laptops (Python standard library only) |
| `hardware_test/real_host_probe.py` | Single-laptop disclosure probe used in the paper (needs Playwright) |
| `hardware_test/real_host_results.jsonl` | Its 60 results (address classes only) |
| `hardware_test/Phone_Test_Steps.pdf` | Phone test steps for a **Windows** laptop |
| `hardware_test/Hardware_Setup_Steps.pdf` | Full router + cable test (client isolation), any OS |

---

## Phone test with a MacBook and an Android phone

No cable and no router needed. One phone makes a hotspot; the MacBook runs the test page; each phone (and the Mac's own browsers) is checked one at a time. It shows what each browser reveals about its own network address. It does **not** test client isolation (a hotspot has no such setting).

Only test your own devices, or friends' devices with their agreement. Do not use the college Wi-Fi without IT permission.

### Part 1: Get the files on the Mac (once)

1. Open this repository on GitHub, press the green **Code** button, then **Download ZIP**.
2. Double-click the ZIP in Downloads to unpack it. You get a folder `cnpaperwork-main`.

### Part 2: Start the test page on the Mac (once)

1. On the Android phone: **Settings → Connections (or Network & internet) → Hotspot & tethering → Wi-Fi hotspot → On**. Mobile data must be on.
2. On the Mac, click the Wi-Fi icon in the menu bar and join that hotspot.
3. Open **Terminal** (press `Cmd + Space`, type `Terminal`, press Enter).
4. Type `cd ` (with a space after it), then **drag the `hardware_test` folder** from Finder into the Terminal window, and press **Enter**.
5. Type `python3 server.py` and press **Enter**.
   - If the Mac asks to install "command line developer tools", click **Install**, wait, then run `python3 server.py` again.
6. Terminal prints an address such as `http://192.168.43.57:8000/`. Write it down.
7. If macOS asks *"Do you want the application python3 to accept incoming network connections?"*, click **Allow**.
8. Leave Terminal open until you finish.

### Part 3: Test each Android phone

1. Connect the phone to the **same hotspot**. (The hotspot phone itself can be tested too; if the page does not open on it, skip it.)
2. Open the browser to test: **Chrome**, **Firefox** (install from Play Store if you can), or **Samsung Internet**.
3. Type the address from Part 2 exactly, including `:8000`. A page called **WebRTC path test** opens.
4. **This device and browser**: type the phone and browser, e.g. `Samsung A54 Chrome`.
5. **Network setup**: type `hotspot`.
6. Press **Test this device only** and wait about 10 seconds.
7. The page shows **Result: HIDES local address** or **Result: REVEALS local address**. It is saved on the Mac automatically.
8. **Reload the page and press the button again**, so each phone + browser is tested **3 times**.
9. Repeat for every browser on every phone.

An iPhone works the same way with Safari, if anyone has one. iPhone results are the most valuable, because no study we cite measured current iOS.

### Part 4: Test the Mac's own browsers

1. On the Mac, open **Safari** and go to `http://localhost:8000/`.
2. Fill the two boxes (e.g. `MacBook Safari`, `hotspot`) and press **Test this device only**, 3 times (reload between).
3. Do the same in **Chrome** or **Firefox** if installed.

### Part 5: Write down (once per device)

| Device model | OS version | Browser and version | Results (3 runs) |
|---|---|---|---|
| | | | |

- Android version: **Settings → About phone → Software information**.
- Chrome version: **⋮ → Settings → About Chrome**. Firefox: **⋮ → Settings → About Firefox**.
- macOS: **Apple menu → About This Mac**. Safari: **Safari → About Safari**.

### Part 6: Finish

1. In Terminal press **Ctrl + C**.
2. The file `hardware_test/results.jsonl` now holds the results (address *types* only, never the actual addresses).
3. Upload it here: open the `hardware_test` folder on GitHub → **Add file → Upload files** (needs collaborator access), or send it to Shubhi.

### If something goes wrong

| Problem | Fix |
|---|---|
| Terminal prints `<this computer's address>` | Run `ipconfig getifaddr en0` in Terminal and use `http://<that address>:8000/` |
| Page does not open on the phone | Same hotspot? Try the other printed address. macOS: **System Settings → Network → Firewall** – allow python3 or turn the firewall off for the test. |
| `Address already in use` | A previous server is still running. Close all Terminal windows and start again. |
| Result: NO CANDIDATES | Turn mobile data on; the hotspot needs internet. |

### Optional: two-device test

On two devices, use the same **Room number**, press **Start as A** on one and **Join as B** on the other within two minutes. This records whether the two devices connected directly. Without a router you can only run the "isolation OFF" case.

---

## Windows

Follow `hardware_test/Phone_Test_Steps.pdf`. It is the same test, with `python server.py` started from a Command Prompt in the `hardware_test` folder.

## Rebuilding the paper

`pdflatex WebRTC_Campus_Threat_Model`, `bibtex WebRTC_Campus_Threat_Model`, then `pdflatex` twice, inside `paper_source/`.
