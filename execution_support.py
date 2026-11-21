"""Keep process-pool serialization while making baseline measurement observable."""
from concurrent.futures import Future
import pickle

def install_inline_pools(fast_fail=False,on_error=None):
    import concurrent.futures
    class MeasuredPool:
        def __init__(self, max_workers=None, initializer=None, initargs=(), **options):
            if initializer is not None:
                initializer(*initargs)
        def __enter__(self):
            return self
        def __exit__(self, *error):
            self.shutdown()
        def shutdown(self, wait=True, **options):
            pass
        def submit(self, function, /, *args, **kwargs):
            task=Future()
            task.set_running_or_notify_cancel()
            try:
                fn, positional, named=pickle.loads(pickle.dumps((function,args,kwargs)))
                value=fn(*positional,**named)
                task.set_result(pickle.loads(pickle.dumps(value)))
            except BaseException as error:
                if fast_fail:
                    if on_error:
                        on_error(error)
                    raise RuntimeError('Verification task failed before remaining work') from error
                task.set_exception(error)
            return task
    concurrent.futures.ProcessPoolExecutor=MeasuredPool
