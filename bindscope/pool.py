"""Small physical-session pool and writer-preferring context barrier.

The barrier coordinates only cooperating operations in this process. It is not
an external-DDL detector and does not claim database-wide atomicity.
"""
from __future__ import annotations
import queue
import threading
import time
from contextlib import contextmanager

class RWGate:
    def __init__(self):
        self.cv=threading.Condition(); self.readers=0; self.writer=False; self.waiting_writers=0
    @contextmanager
    def read(self):
        with self.cv:
            while self.writer or self.waiting_writers:self.cv.wait()
            self.readers+=1
        try:yield
        finally:
            with self.cv:
                self.readers-=1; self.cv.notify_all()
    @contextmanager
    def write(self):
        with self.cv:
            self.waiting_writers+=1
            try:
                while self.writer or self.readers:self.cv.wait()
                self.writer=True
            finally:self.waiting_writers-=1
        try:yield
        finally:
            with self.cv:self.writer=False; self.cv.notify_all()

class Pool:
    def __init__(self, factory, size:int=2):
        if size<1:raise ValueError('pool size must be positive')
        self.queue=queue.Queue(maxsize=size); self.sessions=[]; self.lock=threading.Lock()
        self.closed=False; self.active=set(); self.wait_ns=[]; self.leases=0; self.gate=RWGate()
        try:
            for i in range(size):
                session=factory(str(i)); self.sessions.append(session); self.queue.put(session)
        except Exception:
            for session in self.sessions:
                if hasattr(session,'close'):session.close()
            raise
    @contextmanager
    def lease(self,timeout:float=10):
        with self.lock:
            if self.closed:raise RuntimeError('pool closed')
        start=time.perf_counter_ns(); session=self.queue.get(timeout=timeout)
        with self.lock:
            if self.closed:
                self.queue.put(session); raise RuntimeError('pool closed')
            if id(session) in self.active:raise AssertionError('simultaneous physical-session use')
            self.active.add(id(session)); self.wait_ns.append(time.perf_counter_ns()-start); self.leases+=1
        try:yield session
        finally:
            with self.lock:self.active.remove(id(session))
            self.queue.put(session)
    def broadcast(self,event):
        # Call while holding gate.write(); each session has its own guard.
        for session in self.sessions:session.guard.observe(event)
    def close(self):
        with self.lock:
            if self.active:raise RuntimeError('cannot close pool with active leases')
            self.closed=True
            for session in self.sessions:
                if hasattr(session,'close'):session.close()
