"""Reject Python socket/DNS operations in local candidate verification."""
import sys

def install():
    def reject(event,arguments):
        if event in ('socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr','socket.sendto'):
            raise PermissionError('Candidate verification has no Python network access')
    sys.addaudithook(reject)
