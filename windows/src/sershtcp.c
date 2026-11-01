/* sershtcp.c - remote control shell over TCP (Winsock 1.1).
 * Runs on Windows 95, 98, ME, NT4, 2000 and XP.
 *
 * Runs alongside Dial-Up Networking: control + internet share the one PPP
 * link because both are just IP. Runs commands via %COMSPEC% (command.com on
 * 9x, cmd.exe on NT) and moves files with length-prefixed transfers.
 *
 *   SERSHTCP.EXE [/port N] [/allow IP|any] [/hide]
 *   SERSHTCP.EXE /install [/port N] [/allow IP|any]   start at every logon
 *   SERSHTCP.EXE /uninstall
 *
 *   /port N     TCP port to listen on (default 2323)
 *   /allow IP   only accept connections from IP (default 192.168.95.1, the
 *               dial-up gateway). "any" accepts everyone - there is NO
 *               password, so only do that on an isolated link.
 *   /hide       detach from the console window (used by /install)
 *
 * Freestanding (-nostdlib): only KERNEL32 / WSOCK32 / ADVAPI32 exports that
 * exist on every Win32 version from 95 on.
 *
 * Protocol (one client at a time; CRLF or LF accepted on command lines):
 *   <cmd>\n            run it; output streamed; then
 *                      \r\n__SERSHELL_DONE rc=<n>__\r\n
 *   :put <name> <size>\n   then <size> raw bytes follow -> written to <name>;
 *                          then the DONE marker.
 *   :get <name>\n          server sends "SIZE <n>\r\n" then <n> raw bytes;
 *                          then the DONE marker.
 *   :quit\n            close this connection.
 */

#include <winsock.h>
#include "common.h"

#define MARKER "\r\n__SERSHELL_DONE rc="
#define RUNKEY "Software\\Microsoft\\Windows\\CurrentVersion\\Run"
#define RUNVAL "sershtcp"

static SOCKET g_cs;

/* ---- socket I/O ---- */
static void sput(const char *buf, int n){
    int w;
    while(n>0){ w=send(g_cs,buf,n,0); if(w<=0) return; buf+=w; n-=w; }
}
static void sputs(const char *s){ sput(s,(int)slen(s)); }

/* buffered reader so a command line and the binary that follows don't tangle */
static char rbuf[2048]; static int rlen=0, rpos=0;
static int fill(void){
    if(rpos<rlen) return 1;
    rpos=0; rlen=recv(g_cs,rbuf,sizeof(rbuf),0);
    return (rlen>0);
}
static int rbyte(void){ if(!fill()) return -1; return (unsigned char)rbuf[rpos++]; }
static int readn(unsigned char *out, DWORD n){            /* exactly n bytes */
    DWORD got=0; int c;
    while(got<n){
        if(rpos<rlen){
            DWORD take=rlen-rpos; if(take>n-got) take=n-got;
            memcpy(out+got,rbuf+rpos,take); rpos+=take; got+=take;
        }else{
            c=rbyte(); if(c<0) return 0; out[got++]=(unsigned char)c;
        }
    }
    return 1;
}
static int getline_(char *buf, int cap){
    int i=0,c;
    for(;;){
        c=rbyte(); if(c<0) return -1;
        if(c=='\r') continue;
        if(c=='\n'){ buf[i]=0; return i; }
        if(i<cap-1) buf[i++]=(char)c;
    }
}

static void send_done(DWORD code){
    char d[64]; DWORD p=0,i; for(i=0;MARKER[i];i++) d[p++]=MARKER[i];
    putnum(d,&p,code); d[p++]='_'; d[p++]='_'; d[p++]='\r'; d[p++]='\n'; sput(d,(int)p);
}

/* ---- run a command via %COMSPEC%, stream output to the socket ---- */
static void run_command(const char *cmdline){
    SECURITY_ATTRIBUTES sa; HANDLE rd,rd2,wr; STARTUPINFO si; PROCESS_INFORMATION pi;
    static char full[1400]; static char buf[512]; char cs[260]; DWORD p=0,i,r; DWORD code=0;
    HANDLE self=GetCurrentProcess();

    sa.nLength=sizeof(sa); sa.lpSecurityDescriptor=0; sa.bInheritHandle=TRUE;
    if(!CreatePipe(&rd,&wr,&sa,0)){ sputs("\r\n[pipe failed]"); send_done(255); return; }
    if(DuplicateHandle(self,rd,self,&rd2,0,FALSE,DUPLICATE_SAME_ACCESS)){ CloseHandle(rd); rd=rd2; }
    get_comspec(cs,sizeof(cs));
    scat(full,&p,cs); scat(full,&p," /c ");
    for(i=0;cmdline[i]&&p<sizeof(full)-1;i++) full[p++]=cmdline[i];
    full[p]=0;
    ZeroMemory(&si,sizeof(si)); si.cb=sizeof(si);
    si.dwFlags=STARTF_USESTDHANDLES|STARTF_USESHOWWINDOW; si.wShowWindow=SW_HIDE;
    si.hStdOutput=wr; si.hStdError=wr; si.hStdInput=GetStdHandle(STD_INPUT_HANDLE);
    if(!CreateProcess(0,full,0,0,TRUE,CREATE_NEW_CONSOLE,0,0,&si,&pi)){
        CloseHandle(rd); CloseHandle(wr); sputs("\r\n[CreateProcess failed]"); send_done(255); return;
    }
    CloseHandle(wr);
    for(;;){
        DWORD avail=0; PeekNamedPipe(rd,0,0,0,&avail,0);
        if(avail){ if(avail>sizeof(buf)) avail=sizeof(buf);
                   if(ReadFile(rd,buf,avail,&r,0)&&r) sput(buf,(int)r); }
        else if(WaitForSingleObject(pi.hProcess,0)==WAIT_OBJECT_0){
            PeekNamedPipe(rd,0,0,0,&avail,0); if(!avail) break;
        } else Sleep(10);
    }
    GetExitCodeProcess(pi.hProcess,&code);
    CloseHandle(pi.hProcess); CloseHandle(pi.hThread); CloseHandle(rd);
    send_done(code);
}

static void do_put(char *name, DWORD size){
    HANDLE f; static unsigned char buf[1024]; DWORD got=0,w; int c,bi=0; int ok=1;
    f=CreateFile(name,GENERIC_WRITE,0,0,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,0);
    if(f==INVALID_HANDLE_VALUE){ send_done(254); return; }
    /* read byte-by-byte via rbyte (the proven reader), buffer, flush to disk */
    while(got<size){
        c=rbyte();
        if(c<0){ ok=0; break; }
        buf[bi++]=(unsigned char)c; got++;
        if(bi==(int)sizeof(buf)){ WriteFile(f,buf,bi,&w,0); bi=0; }
    }
    if(bi) WriteFile(f,buf,bi,&w,0);
    CloseHandle(f);
    send_done(ok?0:253);
}
static void do_get(char *name){
    HANDLE f; static unsigned char buf[1024]; DWORD sz,r; char hdr[32]; DWORD hp=0;
    f=CreateFile(name,GENERIC_READ,FILE_SHARE_READ,0,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,0);
    if(f==INVALID_HANDLE_VALUE){ sputs("SIZE 0\r\n"); send_done(254); return; }
    sz=GetFileSize(f,0);
    hdr[hp++]='S';hdr[hp++]='I';hdr[hp++]='Z';hdr[hp++]='E';hdr[hp++]=' ';
    putnum(hdr,&hp,sz); hdr[hp++]='\r'; hdr[hp++]='\n'; sput(hdr,(int)hp);
    for(;;){ if(!ReadFile(f,buf,sizeof(buf),&r,0)||r==0) break; sput((char*)buf,(int)r); }
    CloseHandle(f);
    send_done(0);
}

static void serve(void){
    static char line[1200]; char *av[4]; int n, ac;
    rlen=rpos=0;
    sputs("\r\nsershell-tcp ready. cmd | :put f n | :get f | :quit\r\n");
    for(;;){
        sputs("\r\nC> ");
        n=getline_(line,sizeof(line));
        if(n<0) return;
        if(n==0) continue;
        if(line[0]==':'){
            if(line[1]=='q') return;
            if(line[1]=='p'){ ac=split(line,av,4); if(ac>=3) do_put(av[1],atou(av[2])); else send_done(253); continue; }
            if(line[1]=='g'){ ac=split(line,av,4); if(ac>=2) do_get(av[1]); else send_done(253); continue; }
        }
        run_command(line);
    }
}


/* ---- autostart: HKLM\...\Run works on 95 through XP ---- */
static void say(const char *s){ DWORD w; WriteFile(GetStdHandle(STD_OUTPUT_HANDLE),s,slen(s),&w,0); }

static int install(DWORD port, const char *allow){
    static char cmd[600]; char exe[MAX_PATH]; DWORD p=0; HKEY k; LONG r;
    GetModuleFileName(0,exe,sizeof(exe));
    cmd[0]=0; scat(cmd,&p,"\""); scat(cmd,&p,exe); scat(cmd,&p,"\" /hide /port ");
    putnum(cmd,&p,port); cmd[p]=0; scat(cmd,&p," /allow "); scat(cmd,&p,allow);
    r=RegCreateKeyEx(HKEY_LOCAL_MACHINE,RUNKEY,0,0,0,KEY_SET_VALUE,0,&k,0);
    if(r!=ERROR_SUCCESS){ say("Could not open the Run key (on 2000/XP, run as Administrator).\r\n"); return 1; }
    r=RegSetValueEx(k,RUNVAL,0,REG_SZ,(const BYTE*)cmd,slen(cmd)+1);
    RegCloseKey(k);
    if(r!=ERROR_SUCCESS){ say("Could not write the Run key.\r\n"); return 1; }
    say("Installed: starts at every logon as\r\n  "); say(cmd); say("\r\n");
    /* Windows XP SP2+ firewall: allow inbound for this program (ignored elsewhere) */
    if(is_nt()){
        static char fw[700]; STARTUPINFO si; PROCESS_INFORMATION pi; DWORD q=0;
        fw[0]=0; scat(fw,&q,"netsh firewall add allowedprogram program=\""); scat(fw,&q,exe);
        scat(fw,&q,"\" name=sershtcp mode=ENABLE");
        ZeroMemory(&si,sizeof(si)); si.cb=sizeof(si); si.dwFlags=STARTF_USESHOWWINDOW; si.wShowWindow=SW_HIDE;
        if(CreateProcess(0,fw,0,0,FALSE,0,0,0,&si,&pi)){
            WaitForSingleObject(pi.hProcess,15000); CloseHandle(pi.hProcess); CloseHandle(pi.hThread);
            say("Firewall exception requested (Windows XP SP2+).\r\n");
        }
    }
    say("Start it now with:  "); say(cmd); say("\r\n");
    return 0;
}
static int uninstall(void){
    HKEY k;
    if(RegOpenKeyEx(HKEY_LOCAL_MACHINE,RUNKEY,0,KEY_SET_VALUE,&k)==ERROR_SUCCESS){
        RegDeleteValue(k,RUNVAL); RegCloseKey(k);
    }
    say("Autostart removed.\r\n");
    return 0;
}

void entry(void){
    WSADATA w; SOCKET ls; struct sockaddr_in a; int al, one=1, i;
    char *args[12]; int na=get_args(args,12);
    DWORD port=2323; const char *allow="192.168.95.1"; unsigned long allow_ip;
    int hide=0, do_inst=0, do_uninst=0;

    for(i=0;i<na;i++){
        if(ieq(args[i],"/port")&&i+1<na) port=atou(args[++i]);
        else if(ieq(args[i],"/allow")&&i+1<na) allow=args[++i];
        else if(ieq(args[i],"/hide")) hide=1;
        else if(ieq(args[i],"/install")) do_inst=1;
        else if(ieq(args[i],"/uninstall")) do_uninst=1;
        else if(ieq(args[i],"/?")||ieq(args[i],"/help")||ieq(args[i],"-h")){
            say("SERSHTCP [/port N] [/allow IP|any] [/hide] | /install [...] | /uninstall\r\n");
            ExitProcess(0);
        }
    }
    if(do_uninst) ExitProcess(uninstall());
    if(do_inst)   ExitProcess(install(port,allow));
    if(hide) FreeConsole();

    if(WSAStartup(0x0101,&w)) ExitProcess(1);
    allow_ip = ieq(allow,"any") ? INADDR_NONE : inet_addr(allow);
    ls=socket(AF_INET,SOCK_STREAM,0);
    if(ls==INVALID_SOCKET){ WSACleanup(); ExitProcess(2); }
    setsockopt(ls,SOL_SOCKET,SO_REUSEADDR,(char*)&one,sizeof(one));
    a.sin_family=AF_INET; a.sin_port=htons((u_short)port); a.sin_addr.s_addr=INADDR_ANY;
    if(bind(ls,(struct sockaddr*)&a,sizeof(a))){ WSACleanup(); ExitProcess(3); }
    listen(ls,1);
    for(;;){
        al=sizeof(a);
        g_cs=accept(ls,(struct sockaddr*)&a,&al);
        if(g_cs==INVALID_SOCKET){ Sleep(200); continue; }
        if(allow_ip!=INADDR_NONE && a.sin_addr.s_addr!=allow_ip){ closesocket(g_cs); continue; }
        serve();
        closesocket(g_cs);
    }
}
