/* sershell.c - serial command shell + XMODEM-1K/CRC file transfer.
 * Runs on Windows 95, 98, ME, NT4, 2000 and XP.
 *
 *   SERSHELL.EXE [COMn] [baud]        default COM1 115200 8N1
 *
 * Freestanding (-nostdlib): only KERNEL32 exports common to 9x and NT.
 *
 * Line protocol (CRLF or LF accepted):
 *   <command>\n            run via %COMSPEC% (command.com / cmd.exe), output streamed back, then
 *                          \r\n__SERSHELL_DONE rc=<n>__\r\n
 *   :recv <name> <size>\n  receive file over XMODEM, write <name>, truncate
 *                          to <size>; then the DONE marker (rc=0 ok).
 *   :send <name>\n         print "SIZE <n>\r\n", then send <name> over XMODEM;
 *                          then the DONE marker.
 *   :quit\n                exit.
 * The matching Linux client (serctl.py) drives sx/rx around these commands.
 */

#include "common.h"

static char  g_port[32]="COM1";
static DWORD g_baud=115200;
#define MARKER  "\r\n__SERSHELL_DONE rc="

#define SOH  0x01
#define STX  0x02
#define EOT  0x04
#define ACKB 0x06
#define NAKB 0x15
#define CANB 0x18
#define CRCC 'C'
#define SUBB 0x1A

static HANDLE g_com;


static void sput(const char *buf, DWORD n){
    DWORD w;
    while(n){ if(!WriteFile(g_com,buf,n,&w,0)||!w) return; buf+=w; n-=w; }
}
static void sputs(const char *s){ sput(s,slen(s)); }
static void sbyte(int b){ char c=(char)b; DWORD w; WriteFile(g_com,&c,1,&w,0); }

/* read one byte, waiting up to `ticks` read timeouts (~500ms each).
 * returns 0..255, or -1 on timeout. */
static int rbyte(int ticks){
    DWORD r; unsigned char c; int t;
    for(t=0;t<ticks;t++){
        if(!ReadFile(g_com,&c,1,&r,0)) return -1;
        if(r==1) return c;
    }
    return -1;
}
/* read exactly n bytes into buf with a per-byte timeout; 1 on success */
static int readn(unsigned char *buf, int n){
    int c, i;
    for(i=0;i<n;i++){ c=rbyte(4); if(c<0) return 0; buf[i]=(unsigned char)c; }
    return 1;
}
static void drain(void){ while(rbyte(1)>=0){} }

static unsigned crc16(const unsigned char *p, int n){
    unsigned crc=0; int i,j;
    for(i=0;i<n;i++){
        crc ^= ((unsigned)p[i])<<8;
        for(j=0;j<8;j++) crc = (crc&0x8000)?((crc<<1)^0x1021):(crc<<1);
    }
    return crc & 0xFFFF;
}

static void send_done(DWORD code){
    char done[64]; DWORD dp=0, i;
    for(i=0;MARKER[i];i++) done[dp++]=MARKER[i];
    putnum(done,&dp,code);
    done[dp++]='_'; done[dp++]='_'; done[dp++]='\r'; done[dp++]='\n';
    sput(done,dp);
}

/* ---- XMODEM-1K/CRC receiver: sershell asks, remote (sx) sends ---- */
static int xrecv(HANDLE f, DWORD want){
    unsigned char blk[1024];
    int c,b1,b2,ch,cl,bs,t; unsigned exp=1, crc; DWORD w, written=0;

    for(t=0;t<20;t++){ sbyte(CRCC); c=rbyte(6); if(c>=0) break; }
    if(c<0) return 1;
    for(;;){
        if(c==EOT){ sbyte(ACKB); break; }
        if(c==CANB){ return 2; }
        if(c!=SOH && c!=STX){ drain(); sbyte(NAKB); c=rbyte(8); if(c<0) return 3; continue; }
        bs = (c==STX)?1024:128;
        b1=rbyte(4); b2=rbyte(4);
        if(!readn(blk,bs)){ drain(); sbyte(NAKB); c=rbyte(8); if(c<0) return 3; continue; }
        ch=rbyte(4); cl=rbyte(4);
        crc=crc16(blk,bs);
        if(b1>=0&&b2>=0&&((b1+b2)==255)&&ch>=0&&cl>=0&&((unsigned)((ch<<8)|cl)==crc)){
            if((unsigned)(b1&0xff)==(exp&0xff)){
                DWORD n=bs; if(written+n>want) n=want-written;
                if(n){ WriteFile(f,blk,n,&w,0); written+=n; }
                sbyte(ACKB); exp++;
            }else if((unsigned)(b1&0xff)==((exp-1)&0xff)){
                sbyte(ACKB);               /* duplicate, re-ack */
            }else{
                sbyte(NAKB);
            }
        }else{
            sbyte(NAKB);
        }
        c=rbyte(8); if(c<0) return 3;
    }
    return 0;
}

/* ---- XMODEM-1K sender: remote (rx) asks, sershell sends.
 * Honors whichever mode the receiver requests: 'C' => CRC16 (2 bytes),
 * NAK => classic 8-bit checksum (1 byte). ---- */
static int xsend(HANDLE f){
    unsigned char blk[1024]; DWORD n,i; unsigned bn=1, crc; int c,t,tries,use_crc=1,sum;
    for(t=0;t<30;t++){ c=rbyte(6); if(c==CRCC){use_crc=1;break;} if(c==NAKB){use_crc=0;break;} }
    if(c!=CRCC&&c!=NAKB) return 1;
    for(;;){
        if(!ReadFile(f,blk,1024,&n,0)||n==0) break;
        for(i=n;i<1024;i++) blk[i]=SUBB;
        crc=crc16(blk,1024);
        sum=0; for(i=0;i<1024;i++) sum=(sum+blk[i])&0xff;
        for(tries=0;tries<10;tries++){
            sbyte(STX); sbyte(bn&0xff); sbyte((~bn)&0xff);
            sput((char*)blk,1024);
            if(use_crc){ sbyte((crc>>8)&0xff); sbyte(crc&0xff); }
            else        { sbyte(sum); }
            c=rbyte(8);
            if(c==ACKB) break;
            if(c==CANB) return 2;
        }
        if(tries>=10) return 3;
        bn++;
    }
    for(t=0;t<10;t++){ sbyte(EOT); c=rbyte(8); if(c==ACKB) break; }
    return 0;
}

static int sgetline(char *buf, DWORD cap){
    DWORD i=0, r; char c;
    for(;;){
        if(!ReadFile(g_com,&c,1,&r,0)) return -1;
        if(r==0) continue;
        if(c=='\r') continue;
        if(c=='\n'){ buf[i]=0; return (int)i; }
        if(i<cap-1) buf[i++]=c;
    }
}

static void run_command(const char *cmdline){
    SECURITY_ATTRIBUTES sa; HANDLE rd,rd2,wr; STARTUPINFO si; PROCESS_INFORMATION pi;
    static char full[1400]; char cs[260]; DWORD p=0, i, r; char buf[512]; DWORD code=0;
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
        DWORD avail=0;
        PeekNamedPipe(rd,0,0,0,&avail,0);
        if(avail){ if(avail>sizeof(buf)) avail=sizeof(buf);
                   if(ReadFile(rd,buf,avail,&r,0)&&r) sput(buf,r); }
        else if(WaitForSingleObject(pi.hProcess,0)==WAIT_OBJECT_0){
            PeekNamedPipe(rd,0,0,0,&avail,0); if(!avail) break;
        } else Sleep(10);
    }
    GetExitCodeProcess(pi.hProcess,&code);
    CloseHandle(pi.hProcess); CloseHandle(pi.hThread); CloseHandle(rd);
    send_done(code);
}

static void do_recv(char *name, DWORD size){
    HANDLE f; int rc;
    f=CreateFile(name,GENERIC_WRITE,0,0,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,0);
    if(f==INVALID_HANDLE_VALUE){ send_done(254); return; }
    rc=xrecv(f,size);
    SetFilePointer(f,size,0,FILE_BEGIN); SetEndOfFile(f);   /* exact length */
    CloseHandle(f);
    send_done(rc);
}
static void do_send(char *name){
    HANDLE f; char hdr[32]; DWORD hp=0, sz; int rc;
    f=CreateFile(name,GENERIC_READ,FILE_SHARE_READ,0,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,0);
    if(f==INVALID_HANDLE_VALUE){ send_done(254); return; }
    sz=GetFileSize(f,0);
    hdr[hp++]='S';hdr[hp++]='I';hdr[hp++]='Z';hdr[hp++]='E';hdr[hp++]=' ';
    putnum(hdr,&hp,sz); hdr[hp++]='\r'; hdr[hp++]='\n'; sput(hdr,hp);
    rc=xsend(f);
    CloseHandle(f);
    send_done(rc);
}

void entry(void){
    DCB dcb; COMMTIMEOUTS ct; static char line[1200]; char *av[4]; int n, ac, i;
    char *args[4]; int na=get_args(args,4); char dev[48]; DWORD dp=0; static char banner[128]; DWORD bp=0;

    for(i=0;i<na;i++){
        if(lc(args[i][0])=='c'&&lc(args[i][1])=='o'&&lc(args[i][2])=='m'){
            DWORD k=0; while(args[i][k]&&k<sizeof(g_port)-1){ g_port[k]=args[i][k]; k++; } g_port[k]=0;
        } else if(args[i][0]>='0'&&args[i][0]<='9') g_baud=atou(args[i]);
    }
    /* NT needs \\.\ for COM10+; it is harmless for COM1-9. 9x rejects it. */
    dev[0]=0; if(is_nt()) scat(dev,&dp,"\\\\.\\"); scat(dev,&dp,g_port);
    g_com=CreateFile(dev,GENERIC_READ|GENERIC_WRITE,0,0,OPEN_EXISTING,0,0);
    if(g_com==INVALID_HANDLE_VALUE) ExitProcess(1);

    SetupComm(g_com,8192,8192);
    ZeroMemory(&dcb,sizeof(dcb)); dcb.DCBlength=sizeof(dcb);
    GetCommState(g_com,&dcb);
    dcb.BaudRate=g_baud; dcb.ByteSize=8; dcb.Parity=NOPARITY; dcb.StopBits=ONESTOPBIT;
    dcb.fBinary=TRUE; dcb.fParity=FALSE;
    dcb.fOutxCtsFlow=FALSE; dcb.fOutxDsrFlow=FALSE; dcb.fDsrSensitivity=FALSE;
    dcb.fOutX=FALSE; dcb.fInX=FALSE;
    dcb.fDtrControl=DTR_CONTROL_ENABLE; dcb.fRtsControl=RTS_CONTROL_ENABLE;
    SetCommState(g_com,&dcb);

    ct.ReadIntervalTimeout=50; ct.ReadTotalTimeoutMultiplier=0;
    ct.ReadTotalTimeoutConstant=500; ct.WriteTotalTimeoutMultiplier=0;
    ct.WriteTotalTimeoutConstant=5000;
    SetCommTimeouts(g_com,&ct);

    scat(banner,&bp,"\r\nsershell ready on "); scat(banner,&bp,g_port); scat(banner,&bp," @");
    putnum(banner,&bp,g_baud); banner[bp]=0; scat(banner,&bp," 8N1. cmd | :recv f n | :send f | :quit\r\n");
    sputs(banner);
    for(;;){
        sputs("\r\nC> ");
        n=sgetline(line,sizeof(line));
        if(n<0) break;
        if(n==0) continue;
        if(line[0]==':'){
            if(line[1]=='q') break;
            if(line[1]=='r'){ ac=split(line,av,4); if(ac>=3) do_recv(av[1],atou(av[2])); else send_done(253); continue; }
            if(line[1]=='s'){ ac=split(line,av,4); if(ac>=2) do_send(av[1]); else send_done(253); continue; }
        }
        run_command(line);
    }
    sputs("\r\nsershell exiting.\r\n");
    CloseHandle(g_com);
    ExitProcess(0);
}
