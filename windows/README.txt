RETRO DIAL-UP GATEWAY - Windows client tools
=============================================
For Windows 95, 98, ME, NT 4.0, 2000 and XP (one set of files runs on all).

  SERSHTCP.EXE  Remote control over the dial-up (TCP) link. Lets the Linux
                gateway run commands and copy files while you browse.
  SERSHELL.EXE  Remote control over the bare serial cable, for when the PC
                is NOT dialed in.  Usage: SERSHELL [COMn] [baud]
                (default COM1 115200).
  REBOOT.EXE    Forces an immediate restart (handy from the remote shell).

All three are tiny (under 10 KB), need no installer, no DLLs and no runtime.
Copy them to C:\ with a floppy, CD, or SERSHELL's serial file transfer.


1. MAKE THE "MODEM"
-------------------
The Linux machine pretends to be a modem on the serial cable. Install a
generic modem driver on the COM port the cable is plugged into.

Windows 95 / 98 / ME
  Control Panel > Modems > Add...
  Tick "Don't detect my modem; I will select it from a list" > Next
  Manufacturer: (Standard Modem Types)   Model: Standard 33600 bps Modem
  Pick the COM port > Finish.
  Then Modems > Properties > Maximum speed: 115200.

Windows 2000 / XP
  Control Panel > Phone and Modem Options > Modems > Add...
  Tick "Don't detect my modem; I will select it from a list" > Next
  (Standard Modem Types) > Standard 33600 bps Modem > pick COM port > Finish.
  Then Properties > Modem > Maximum Port Speed: 115200.


2. MAKE THE DIAL-UP CONNECTION
------------------------------
Windows 95 / 98 / ME
  My Computer > Dial-Up Networking > Make New Connection.
  (Windows 95: if Dial-Up Networking is missing, install it from
   Control Panel > Add/Remove Programs > Windows Setup > Communications.)
  Choose the Standard 33600 modem, any phone number (e.g. 5551212).
  Right-click the connection > Properties > Server Types:
    Type: PPP: Internet, Windows NT Server, Windows 98
    Untick "Log on to network" and "Require encrypted password"
    Protocols: TCP/IP only (untick NetBEUI and IPX/SPX)
    TCP/IP Settings: server-assigned IP and name servers,
                     tick "Use default gateway on remote network"

Windows 2000
  Network and Dial-up Connections > Make New Connection >
  Dial-up to the Internet > "I want to set up my connection manually" >
  "I connect through a phone line and a modem" > any number > any user name.
  Properties > Security: Typical, "Allow unsecured password".

Windows XP
  Network Connections > Create a new connection > Connect to the Internet >
  "Set up my connection manually" > "Connect using a dial-up modem" >
  pick the Standard 33600 modem > any ISP name, any number, any user name.

User name and password can be anything; the gateway does not check them.


3. DIAL
-------
Make sure the Linux gateway is running (green "Gateway" dot in the panel),
then double-click the connection > Dial / Connect.
You should get address 192.168.95.2. Test: ping 192.168.95.1


4. BROWSING THE MODERN WEB
--------------------------
Old browsers can't speak modern HTTPS. Set the browser's HTTP proxy to
192.168.95.1 port 8080 and type plain http:// addresses; the gateway fetches
the https:// page for you and slims it down for the slow link.
  Internet Explorer: Tools/View > Internet Options > Connections > Settings
  Netscape: Edit > Preferences > Advanced > Proxies > Manual


5. REMOTE CONTROL (SERSHTCP.EXE)
--------------------------------
Once, to start it at every logon:
    C:\SERSHTCP.EXE /install
  (Windows 2000/XP: run it as an Administrator. On XP SP2 and later this
   also adds a Windows Firewall exception.)
Start it right away without logging off:
    C:\SERSHTCP.EXE /hide
Remove it from logon:
    C:\SERSHTCP.EXE /uninstall

Options:  /port N       listen on TCP port N (default 2323)
          /allow IP     accept only this IP (default 192.168.95.1 = gateway)
          /allow any    accept anyone - NO PASSWORD, isolated links only!

From Linux:  retrogw-ctl "dir c:\"     or the Connect button in the panel.


SECURITY
--------
SERSHTCP and SERSHELL have no password. By default SERSHTCP only answers the
gateway's address, but anything that can reach the PC from that address
can run commands. Use it on a direct cable link, not on a shared network.
