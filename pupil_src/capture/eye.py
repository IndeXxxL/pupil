'''
(*)~----------------------------------------------------------------------------------
 Pupil - eye tracking platform
 Copyright (C) 2012-2015  Pupil Labs

 Distributed under the terms of the CC BY-NC-SA License.
 License details are in the file license.txt, distributed as part of this software.
----------------------------------------------------------------------------------~(*)
'''

import os
from time import sleep
from file_methods import Persistent_Dict
import logging
import numpy as np

#display
from glfw import *
from pyglui import ui,graph
from pyglui.cygl.utils import init as cygl_init
from pyglui.cygl.utils import draw_points as cygl_draw_points
from pyglui.cygl.utils import RGBA as cygl_rgba

# check versions for our own depedencies as they are fast-changing
from pyglui import __version__ as pyglui_version
assert pyglui_version >= '0.1'

#monitoring
import psutil

# helpers/utils
from gl_utils import basic_gl_setup,adjust_gl_view, clear_gl_screen, draw_gl_point_norm,make_coord_system_pixel_based,make_coord_system_norm_based,create_named_texture,draw_named_texture,draw_gl_polyline
from methods import *
from uvc_capture import autoCreateCapture, FileCaptureError, EndofVideoFileError, CameraCaptureError

# Pupil detectors
from pupil_detectors import Canny_Detector,MSER_Detector,Blob_Detector

def eye(g_pool,cap_src,cap_size,eye_id=0):
    """
    Creates a window, gl context.
    Grabs images from a capture.
    Streams Pupil coordinates into g_pool.pupil_queue
    """

    # modify the root logger for this process
    logger = logging.getLogger()
    # remove inherited handlers
    logger.handlers = []
    # create file handler which logs even debug messages
    fh = logging.FileHandler(os.path.join(g_pool.user_dir,'eye.log'),mode='w')
    fh.setLevel(logging.DEBUG)
    # create console handler with a higher log level
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    # create formatter and add it to the handlers
    formatter = logging.Formatter('Eye Process: %(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    formatter = logging.Formatter('EYE Process [%(levelname)s] %(name)s : %(message)s')
    ch.setFormatter(formatter)
    # add the handlers to the logger
    logger.addHandler(fh)
    logger.addHandler(ch)
    # create logger for the context of this function
    logger = logging.getLogger(__name__)


    # Callback functions
    def on_resize(window,w, h):
        active_window = glfwGetCurrentContext()
        glfwMakeContextCurrent(window)
        hdpi_factor = glfwGetFramebufferSize(window)[0]/glfwGetWindowSize(window)[0]
        w,h = w*hdpi_factor, h*hdpi_factor
        g_pool.gui.update_window(w,h)
        graph.adjust_size(w,h)
        adjust_gl_view(w,h)
        # for p in g_pool.plugins:
            # p.on_window_resize(window,w,h)
        glfwMakeContextCurrent(active_window)

    def on_key(window, key, scancode, action, mods):
        g_pool.gui.update_key(key,scancode,action,mods)
        if action == GLFW_PRESS:
            if key == GLFW_KEY_ESCAPE:
                on_close(window)

    def on_char(window,char):
        g_pool.gui.update_char(char)


    def on_button(window,button, action, mods):
        g_pool.gui.update_button(button,action,mods)
        pos = glfwGetCursorPos(window)
        pos = normalize(pos,glfwGetWindowSize(eye_window))
        pos = denormalize(pos,(frame.img.shape[1],frame.img.shape[0]) ) # Position in img pixels

        # handle roi
        # if not atb.TwEventMouseButtonGLFW(button,int(action == GLFW_PRESS)):
        #     if action == GLFW_PRESS:
        #         if bar.display.value ==1:
        #             pos = glfwGetCursorPos(window)
        #             pos = normalize(pos,glfwGetWindowSize(window))
        #             pos = denormalize(pos,(frame.img.shape[1],frame.img.shape[0]) ) # pos in frame.img pixels
        #             u_r.setStart(pos)
        #             bar.draw_roi.value = 1
        #     else:
        #         bar.draw_roi.value = 0

        # if ROI mode - hide and disable the GUI
        # if action == GLFW_PRESS:
        #     if g_pool.display_mode == 'roi':                
        #         pos = glfwGetCursorPos(window)
        #         pos = normalize(pos,glfwGetWindowSize(window))
        #         pos = denormalize(pos,(frame.img.shape[1],frame.img.shape[0]) ) # pos in frame.img pixels
        #         u_r.setStart(pos)
        #         bar.draw_roi.value = 1
        # else:
        #     g_pool.display_mode ==                
    def on_pos(window,x, y):
        hdpi_factor = float(glfwGetFramebufferSize(window)[0]/glfwGetWindowSize(window)[0])
        x,y = x*hdpi_factor,y*hdpi_factor
        g_pool.gui.update_mouse(x,y)

        # norm_pos = normalize((x,y),glfwGetWindowSize(window))
        # fb_x,fb_y = denormalize(norm_pos,glfwGetFramebufferSize(window))
        # if atb.TwMouseMotion(int(fb_x),int(fb_y)):
        #     pass

        # if bar.draw_roi.value == 1:
        #     pos = denormalize(norm_pos,(frame.img.shape[1],frame.img.shape[0]) ) # pos in frame.img pixels
        #     u_r.setEnd(pos)

    def on_scroll(window,x,y):
        g_pool.gui.update_scroll(x,y)

    def on_close(window):
        g_pool.quit.value = True
        logger.info('Process closing from window')


    # load session persistent settings
    session_settings = Persistent_Dict(os.path.join(g_pool.user_dir,'user_settings_eye'))

    # Initialize capture
    cap = autoCreateCapture(cap_src, cap_size, 24, timebase=g_pool.timebase)

    # Test capture
    try:
        frame = cap.get_frame()
    except CameraCaptureError:
        logger.error("Could not retrieve image from capture")
        cap.close()
        return

    g_pool.capture = cap

    # any object we attach to the g_pool object *from now on* will only be visible to this process!
    # vars should be declared here to make them visible to the code reader.
    g_pool.window_size = session_settings.get('window_size',1.)
    g_pool.display_mode = session_settings.get('display_mode','camera_image')
    g_pool.draw_pupil = session_settings.get('draw_pupil',True)


    u_r = Roi(frame.img.shape)
    g_pool.roi = session_settings.get('roi',u_r)

    writer = None

    pupil_detector = Canny_Detector(g_pool)


    # UI callback functions
    def set_window_size(size):
        hdpi_factor = glfwGetFramebufferSize(eye_window)[0]/glfwGetWindowSize(eye_window)[0]
        w,h = int(frame.width*size*hdpi_factor),int(frame.height*size*hdpi_factor)
        glfwSetWindowSize(eye_window,w,h)

    # Initialize glfw
    glfwInit()
    eye_window = glfwCreateWindow(frame.width, frame.height, "Eye", None, None)
    glfwMakeContextCurrent(eye_window)
    cygl_init()

    # Register callbacks eye_window
    glfwSetWindowSizeCallback(eye_window,on_resize)
    glfwSetWindowCloseCallback(eye_window,on_close)
    glfwSetKeyCallback(eye_window,on_key)
    glfwSetCharCallback(eye_window,on_char)
    glfwSetMouseButtonCallback(eye_window,on_button)
    glfwSetCursorPosCallback(eye_window,on_pos)
    glfwSetScrollCallback(eye_window,on_scroll)

    # gl_state settings
    basic_gl_setup()
    g_pool.image_tex = create_named_texture(frame.img)

    # refresh speed settings
    glfwSwapInterval(0)
    glfwSetWindowPos(eye_window,800,0)


    #setup GUI
    g_pool.gui = ui.UI()
    # g_pool.gui.scale = session_settings.get('gui_scale',1)
    g_pool.sidebar = ui.Scrolling_Menu("Settings",pos=(-300,0),size=(0,0),header_pos='left')
    g_pool.sidebar.configuration = session_settings.get('side_bar_config',{'collapsed':True})
    general_settings = ui.Growing_Menu('General')
    general_settings.configuration = session_settings.get('general_menu_config',{})
    general_settings.append(ui.Selector('display_mode',g_pool,selection=['camera_image','roi','algorithm','cpu_save'], labels=['Camera Image', 'ROI', 'Algorithm', 'CPU Save'], label="Mode") )
    g_pool.sidebar.append(general_settings)
    g_pool.pupil_detector_menu = ui.Growing_Menu('Pupil Detector')
    g_pool.pupil_detector_menu.configuration = session_settings.get('pupil_detector_menu_config',{'collapsed':True})
    g_pool.sidebar.append(g_pool.pupil_detector_menu)

    g_pool.gui.append(g_pool.sidebar)

    # let the camera add its GUI
    g_pool.capture.init_gui(g_pool.sidebar)
    g_pool.capture.menu.configuration = session_settings.get('capture_menu_config',{'collapsed':True})

    # let detectors add their GUI
    pupil_detector.init_gui()

    #set the last saved window size
    set_window_size(g_pool.window_size)
    on_resize(eye_window, *glfwGetWindowSize(eye_window))

    #set up performance graphs
    pid = os.getpid()
    ps = psutil.Process(pid)
    ts = frame.timestamp

    cpu_graph = graph.Bar_Graph()
    cpu_graph.pos = (20,110)
    cpu_graph.update_fn = ps.get_cpu_percent
    cpu_graph.update_rate = 5
    cpu_graph.label = 'CPU %0.1f'

    fps_graph = graph.Bar_Graph()
    fps_graph.pos = (140,110)
    fps_graph.update_rate = 5
    fps_graph.label = "%0.0f FPS"

    # Event loop
    while not g_pool.quit.value:
        # Get an image from the grabber
        try:
            frame = cap.get_frame()
        except CameraCaptureError:
            logger.error("Capture from Camera Failed. Stopping.")
            break
        except EndofVideoFileError:
            logger.warning("Video File is done. Stopping")
            break

        #update performace graphs
        t = frame.timestamp
        dt,ts = t-ts,t
        fps_graph.add(1./dt)
        cpu_graph.update()


        ###  RECORDING of Eye Video (on demand) ###
        # Setup variables and lists for recording
        if g_pool.eye_rx.poll():
            command = g_pool.eye_rx.recv()
            if command is not None:
                record_path = command
                logger.info("Will save eye video to: %s"%record_path)
                video_path = os.path.join(record_path, "eye.mkv")
                timestamps_path = os.path.join(record_path, "eye_timestamps.npy")
                writer = cv2.VideoWriter(video_path, cv2.cv.CV_FOURCC(*'DIVX'), float(cap.frame_rate), (frame.img.shape[1], frame.img.shape[0]))
                timestamps = []
            else:
                logger.info("Done recording eye.")
                writer = None
                np.save(timestamps_path,np.asarray(timestamps))
                del timestamps

        if writer:
            writer.write(frame.img)
            timestamps.append(frame.timestamp)


        # pupil ellipse detection
        result = pupil_detector.detect(frame,user_roi=u_r,visualize=g_pool.display_mode == 'algorithm')
        result['id'] = eye_id
        # stream the result
        g_pool.pupil_queue.put(result)


        # GL drawing
        glfwMakeContextCurrent(eye_window)
        clear_gl_screen()

        # switch to work in normalized coordinate space
        make_coord_system_norm_based()
        if g_pool.display_mode != 'cpu_save':
            draw_named_texture(g_pool.image_tex,frame.img)
        else:
            draw_named_texture(g_pool.image_tex)

        # switch to work in pixel space 
        make_coord_system_pixel_based(frame.img.shape)
        
        if g_pool.display_mode == 'roi':
            draw_gl_polyline(u_r.rect,(.8,.8,.8,0.5),thickness=3)
            cygl_draw_points(u_r.edit_pts,size=36,color=cygl_rgba(.0,.0,.0,.5),sharpness=0.3)
            cygl_draw_points(u_r.edit_pts,size=20,color=cygl_rgba(.5,.5,.9,.9),sharpness=0.9)

        if result['confidence'] >0 and g_pool.draw_pupil:
            if result.has_key('axes'):
                pts = cv2.ellipse2Poly( (int(result['center'][0]),int(result['center'][1])),
                                        (int(result["axes"][0]/2),int(result["axes"][1]/2)),
                                        int(result["angle"]),0,360,15)
                draw_gl_polyline(pts,(1.,0,0,.5))
            draw_gl_point_norm(result['norm_pos'],color=(1.,0.,0.,0.5))

        # render graphs
        graph.push_view()
        fps_graph.draw()
        cpu_graph.draw()
        graph.pop_view()

        # render GUI
        g_pool.gui.update()
        glfwSwapBuffers(eye_window)
        glfwPollEvents()

    # END while running

    # in case eye recording was still runnnig: Save&close
    if writer:
        logger.info("Done recording eye.")
        writer = None
        np.save(timestamps_path,np.asarray(timestamps))


    # save session persistent settings
    session_settings['window_size'] = g_pool.window_size
    session_settings['display_mode'] = g_pool.display_mode
    session_settings['side_bar_config'] = g_pool.sidebar.configuration
    session_settings['capture_menu_config'] = g_pool.capture.menu.configuration
    session_settings['general_menu_config'] = general_settings.configuration
    session_settings['pupil_detector_menu_config'] = g_pool.pupil_detector_menu.configuration
    session_settings.close()

    pupil_detector.cleanup()
    cap.close()
    glfwDestroyWindow(eye_window)
    glfwTerminate()

    #flushing queue in case world process did not exit gracefully
    while not g_pool.pupil_queue.empty():
        g_pool.pupil_queue.get()
    g_pool.pupil_queue.close()

    logger.debug("Process done")

def eye_profiled(g_pool,cap_src,cap_size):
    import cProfile,subprocess,os
    from eye import eye
    cProfile.runctx("eye(g_pool,cap_src,cap_size)",{"g_pool":g_pool,'cap_src':cap_src,'cap_size':cap_size},locals(),"eye.pstats")
    loc = os.path.abspath(__file__).rsplit('pupil_src', 1)
    gprof2dot_loc = os.path.join(loc[0], 'pupil_src', 'shared_modules','gprof2dot.py')
    subprocess.call("python "+gprof2dot_loc+" -f pstats eye.pstats | dot -Tpng -o eye_cpu_time.png", shell=True)
    print "created cpu time graph for eye process. Please check out the png next to the eye.py file"

