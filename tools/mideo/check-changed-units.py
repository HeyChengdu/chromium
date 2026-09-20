#!/usr/bin/env python3
"""提前编译本次修改的单元，避免等待整棵依赖树后才发现接口不匹配。"""
import subprocess
required = {
    'page_handler.o', 'render_widget_host_impl.o', 'software_renderer.o',
    'skia_renderer.o', 'copy_output_request_mojom_traits.o',
    'copy_output_result_mojom_traits.o',
}
listing = subprocess.check_output(['autoninja', '-C', 'out/Mideo', '-t', 'targets', 'all'], text=True)
targets = {line.split(':', 1)[0] for line in listing.splitlines()
           if line.split(':', 1)[0].rsplit('/', 1)[-1] in required}
found = {target.rsplit('/', 1)[-1] for target in targets}
if found != required:
    raise RuntimeError(f'Cannot locate modified build targets: {sorted(required-found)}')
subprocess.run(['autoninja', '-C', 'out/Mideo', '-j', '4', *sorted(targets)], check=True)
