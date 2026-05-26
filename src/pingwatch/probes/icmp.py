"""ICMP probe — raw socket ping (requires CAP_NET_RAW or root)."""

from __future__ import annotations

import asyncio
import os
import struct
import time

from pingwatch.probes import BaseProbe, ProbeResult


class IcmpProbe(BaseProbe):
    """ICMP echo probe using raw sockets."""

    name = "icmp"
    requires_binary = None

    async def run(self, target: str) -> ProbeResult:
        """Send ICMP pings to target."""
        result = ProbeResult(target=target, probe_name=self.name)

        try:
            # Resolve target
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(target, None, family=2)  # AF_INET
            if not infos:
                result.error = f"Cannot resolve {target}"
                return result
            dest_addr = infos[0][4][0]
        except Exception as e:
            result.error = f"DNS resolution failed for {target}: {e}"
            return result

        for seq in range(self.pings):
            try:
                rtt = await self._single_ping(dest_addr, seq + 1)
                result.latencies.append(rtt)
                result.timestamps.append(time.time())
            except TimeoutError:
                result.latencies.append(None)
                result.timestamps.append(time.time())
            except PermissionError:
                result.error = "ICMP requires CAP_NET_RAW or root"
                break
            except Exception as e:
                result.latencies.append(None)
                result.timestamps.append(time.time())

        result.compute_stats()
        return result

    async def _single_ping(self, dest: str, seq: int) -> float:
        """Send one ICMP echo and return RTT in ms. Raises TimeoutError on timeout."""
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        try:
            sock.settimeout(self.timeout)

            # Build ICMP echo request
            pid = os.getpid() & 0xFFFF
            chksum = 0
            header = struct.pack("!BBHHH", 8, 0, chksum, pid, seq)
            data = struct.pack("!d", time.time())
            chksum = self._checksum(header + data)
            header = struct.pack("!BBHHH", 8, 0, chksum, pid, seq)
            packet = header + data

            send_time = time.time()
            sock.sendto(packet, (dest, 0))

            while True:
                recv_packet, addr = sock.recvfrom(1024)
                recv_time = time.time()

                # Parse ICMP reply
                if len(recv_packet) < 28:
                    continue

                icmp_type = recv_packet[20]
                icmp_code = recv_packet[21]
                recv_id = struct.unpack("!H", recv_packet[24:26])[0]
                recv_seq = struct.unpack("!H", recv_packet[26:28])[0]

                if icmp_type == 0 and recv_id == pid and recv_seq == seq:
                    return (recv_time - send_time) * 1000  # ms
        finally:
            sock.close()

        raise TimeoutError

    @staticmethod
    def _checksum(data: bytes) -> int:
        """Compute ICMP checksum."""
        if len(data) % 2:
            data += b"\x00"
        s = 0
        for i in range(0, len(data), 2):
            w = (data[i] << 8) + data[i + 1]
            s += w
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        return ~s & 0xFFFF
