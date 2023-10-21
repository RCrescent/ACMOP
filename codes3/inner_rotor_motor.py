from dataclasses import dataclass
import pyrhonen_procedure_as_function, winding_layout
import numpy as np
import logging
import utility
from utility import acmop_parameter, EPS
from time import time as clock_time
from collections import OrderedDict, namedtuple

# Abbreviations:
# GP = Geometric Parameters
# EX = Other Properties
# SI = Specification Dictionary

def derive_mm_r_si(GP,SI):
    # (option 1) depends on split_ratio and r_os
    # GP       ['mm_r_si'].value = GP['mm_r_so'].value * GP['split_ratio'].value
    # return GP['mm_r_si'].value

        # (option 2) depends on d_sy (which is bad, as d_sy is also derived) and r_os
        # GP['mm_r_si'].value = GP['mm_r_so'].value - GP['mm_d_sy'].value - GP['mm_d_st'].value - GP['mm_d_stt'].value
        # return GP['mm_r_si'].value

    # (option 3) depends on r_or and air gap length
    GP       ['mm_r_si'].value = GP['mm_r_ro'].value + GP['mm_d_sleeve'].value + GP['mm_d_mech_air_gap'].value
    return GP['mm_r_si'].value

def derive_deg_alpha_sto(GP,SI):
    # this allows a square tooth tip, or else the tooth tip might be pointy
    GP       ['deg_alpha_sto'].value = GP['deg_alpha_st'].value/2
    return GP['deg_alpha_sto'].value

def derive_mm_d_stt(GP,SI):
    GP       ['mm_d_stt'].value = 1.5*GP['mm_d_sto'].value
    return GP['mm_d_stt'].value


def derive_mm_d_st(GP,SI):
    GP       ['mm_d_st'].value = GP['mm_r_so'].value - GP['mm_r_si'].value - GP['mm_d_sy'].value - GP['mm_d_stt'].value
    return GP['mm_d_st'].value

def derive_mm_d_sy(GP,SI):
    GP       ['mm_d_sy'].value = GP['mm_r_so'].value - GP['mm_r_si'].value - GP['mm_d_st'].value - GP['mm_d_stt'].value
    return GP['mm_d_sy'].value

def derive_mm_r_so(GP,SI):
    # (option 1)
    # GP       ['mm_r_so'].value = GP['mm_r_si'].value + GP['mm_d_stt'].value + GP['mm_d_st'].value + GP['mm_d_sy'].value
    # return GP['mm_r_so'].value
    # 用d_sy导出r_os可以避免d_sy为负的bug

    # (option 2)
    GP['mm_r_so'].value = GP['mm_r_si'].value / GP['split_ratio'].value
    return GP['mm_r_so'].value


def derive_mm_r_ro(GP,SI):
    GP       ['mm_r_ro'].value = GP['mm_r_si'].value - GP['mm_d_sleeve'].value - GP['mm_d_mech_air_gap'].value
    return GP['mm_r_ro'].value

class template_machine_as_numbers(object):
    ''' # template 有点类似analytical的电机（由几何尺寸组成）
    '''
    def __init__(self, fea_config_dict=None, spec_input_dict=None):

        # 仿真输入
        self.fea_config_dict = fea_config_dict
        self.spec_input_dict = spec_input_dict
        self.SI = spec_input_dict # just a short name

        # 初始化搜索空间
        geometric_parameters = OrderedDict({
            # ROTOR                                  Type       Name                          Value  Bounds       Calc
            "mm_r_ro"           : acmop_parameter("fixed",    "rotor_outer_radius",            None, [None, None], lambda GP,SI:None), #derive_mm_r_ro(GP,SI)),
            "mm_d_mech_air_gap" : acmop_parameter("fixed",    "mechanical_air_gap_length",     None, [None, None], lambda GP,SI:None),
            "mm_d_sleeve"       : acmop_parameter("free",     "sleeve_length",                 None, [None, None], lambda GP,SI:None),
            "split_ratio"       : acmop_parameter("free",     "split_ratio_r_is_slash_r_os",   None, [None, None], lambda GP,SI:None),
            # STATOR                           Type       Name                          Value  Bounds       Calc
            "deg_alpha_st"  : acmop_parameter("free",    "stator_tooth_span_angle"    , None, [None, None], lambda GP,SI:None),
            "mm_w_st"       : acmop_parameter("free",    "stator_tooth_width"         , None, [None, None], lambda GP,SI:None),
            "mm_d_st"       : acmop_parameter("free",    "stator_tooth_depth"         , None, [None, None], lambda GP,SI:derive_mm_d_st(GP,SI)),
            "mm_d_sto"      : acmop_parameter("fixed",   "stator_tooth_open_depth"    , None, [None, None], lambda GP,SI:None),
            "deg_alpha_sto" : acmop_parameter("derived", "stator_tooth_open_angle"    , None, [None, None], lambda GP,SI:derive_deg_alpha_sto(GP,SI)),
            "mm_d_stt"      : acmop_parameter("derived", "stator_tooth_tip_depth"     , None, [None, None], lambda GP,SI:derive_mm_d_stt(GP,SI)),
            "mm_r_si"       : acmop_parameter("derived", "stator_inner_radius"        , None, [None, None], lambda GP,SI:derive_mm_r_si(GP,SI)),
            "mm_r_so"       : acmop_parameter("derived", "stator_outer_radius"        , None, [None, None], lambda GP,SI:derive_mm_r_so(GP,SI)),
            "mm_d_sy"       : acmop_parameter("fixed",   "stator_yoke_depth"          , None, [None, None], lambda GP,SI:derive_mm_d_sy(GP,SI)),
        })
        for k, v in geometric_parameters.items():
            if v.type == 'derived' and v.calc is None:
                raise Exception('calc method is not defined for the derived acmop_parameter:', v)
        # all in one place
        self.d = {
            "which_filter": fea_config_dict['which_filter'],
            "GP": geometric_parameters,
            "EX": OrderedDict(), # "Other Properties" is now renamed to "EXcitations"
            "x_denorm_dict": OrderedDict(),
            "bounds_denorm": [],
        }

        bool_matched = False
        if 'FixedSleeveLength' in self.d['which_filter']:
            self.d['GP']['mm_d_sto'].type = "free"
            self.d['GP']['mm_d_sleeve'].type = "fixed"
            bool_matched = True

        if 'VariableSleeveLength' in self.d['which_filter']:
            self.d['GP']['mm_d_sto'].type = "free"
            self.d['GP']['mm_d_sleeve'].type = "free"
            bool_matched = True

        if 'VariableStatorSlotDepth_VariableStatorYokeDepth' in self.d['which_filter']:
            # IM
            self.d['GP']['mm_d_sto'].type = "free"
            self.d['GP']['mm_d_mech_air_gap'].type = "free"
            bool_matched = True

        if 'VariableSleeveLength_VariableToothTipDepth' == self.d['which_filter']:
            self.d['GP']['mm_d_sto'].type = "fixed"
            self.d['GP']['mm_d_stt'].type = "free"
            self.d['GP']['mm_d_sleeve'].type = "free"
            bool_matched = True

        if 'VariableToothTipDepth_FSPM' == self.d['which_filter']:
            self.d['GP']['mm_d_sto'].type = "fixed"
            self.d['GP']['mm_d_stt'].type = "free"
            self.d['GP']['mm_d_sleeve'].type = "fixed"
            self.d['GP']['mm_w_st'].type = "fixed"
            self.d['GP']['mm_d_st'].type = "derived"
            bool_matched = True

        if bool_matched == False:
            raise Exception(f"Not defined: {self.d['which_filter']}")

        # Get Analytical Design
        # self.ModifiedBianchi2006(fea_config_dict, SI)


        # debug
        # from pprint import pprint
        # for key, val in self.d['GP'].items():
        #     if val.type=='free':
        #         print(key, '\t', val)
        # print('--------------------')
        # for key, val in self.d['GP'].items():
        #     if val.type!='free':
        #         print(key, '\t', val)
        # quit()

    ''' 初始化，定义优化
    '''
    def define_search_space(self, GP, original_template_neighbor_bounds):
        # 定义搜索空间，determine bounds
        self.bounds_denorm = []
        for key, parameter in GP.items(): # Make sure the order of the bounds_denorm is consistent with free parameters' order in GP.
            if parameter.type == 'free':
                parameter.bounds = original_template_neighbor_bounds[key]
                self.bounds_denorm.append(parameter.bounds)
        logging.getLogger().info(f'[inner_rotor_motor.py] template BOUNDS_denorm in R^{len(self.bounds_denorm)}: {self.bounds_denorm}')
        return self.bounds_denorm
    def get_other_properties_after_geometric_parameters_are_initialized(self, GP, SI, specified_mm_stack_length=None):

        # Template's Other Properties (Shared by the swarm)
        EX = self.d['EX']
        if True:
            # WINDING Layout
            if 'Wrap_Around' in self.SI.keys():
                EX['wily'] = wily = winding_layout.winding_layout_v2(SI['DPNV_or_SEPA'], SI['Qs'], SI['p'], SI['ps'], SI['coil_pitch_y'], m=SI['m'], Wrap_Around=SI['Wrap_Around'])
            else:
                EX['wily'] = wily = winding_layout.winding_layout_v2(SI['DPNV_or_SEPA'], SI['Qs'], SI['p'], SI['ps'], SI['coil_pitch_y'], m=SI['m'])
            # STACK LENGTH
            if specified_mm_stack_length is not None:
                EX['mm_template_stack_length'] = specified_mm_stack_length
            else:
                EX['mm_template_stack_length'] = pyrhonen_procedure_as_function.get_mm_template_stack_length(SI, GP['mm_r_ro'].value*1e-3) # mm TODO:
            EX['mm_mechanical_air_gap_length'] = SI['minimum_mechanical_air_gap_length_mm']
            # THERMAL Properties
            EX['Js']                = SI['Js'] # Arms/mm^2 im_OP['Js'] 
            EX['WindingFill']       = SI['WindingFill'] # SI['space_factor_kCu'] is obsolete
            # MOTOR Winding Excitation Properties
            # EX['RotorPoleNumber']   = SI['number_of_rotor_pole_pairs']*2 # this will be overwritten in template
            EX['DriveW_zQ']         =            pyrhonen_procedure_as_function.get_zQ(SI, wily, GP['mm_r_si'].value*2*1e-3, GP['mm_r_ro'].value*2*1e-3, specified_mm_stack_length=specified_mm_stack_length) # TODO:
            # EX['DriveW_CurrentAmp'] = np.sqrt(2)*pyrhonen_procedure_as_function.get_stator_phase_current_rms(SI) # TODO:
            # print('[inner_rotor_motor.py] DriveW_CurrentAmp is initialized as:', EX['DriveW_CurrentAmp'], 'A (considering the specified voltage). This will be overwritten by Js-constraint later.')
            EX['DriveW_Freq']       = SI['ExcitationFreqSimulated']
            EX['DriveW_Rs']         = 1.0 # TODO: Must be greater than zero to let JMAG work
            EX['DriveW_poles']      = SI['p']*2
        # self.d.update( {"EX": EX} )
        return EX

    ''' 实用
    '''
    def get_rotor_volume(self, stack_length=None):
        if stack_length is None:
            return np.pi*(self.d['GP']['mm_r_ro'].value*1e-3)**2 * (self.d['EX']['mm_template_stack_length']*1e-3)
        else:
            return np.pi*(self.d['GP']['mm_r_ro'].value*1e-3)**2 * (stack_length*1e-3)
    def get_rotor_weight(self, gravity=9.8, stack_length=None):
        material_density_rho = pyrhonen_procedure_as_function.get_material_data()[0]
        if stack_length is None:
            return gravity * self.get_rotor_volume() * material_density_rho # steel 7860 or 8050 kg/m^3. Copper/Density 8.96 g/cm³. gravity: 9.8 N/kg
        else:
            return gravity * self.get_rotor_volume(stack_length=stack_length) * material_density_rho # steel 7860 or 8050 kg/m^3. Copper/Density 8.96 g/cm³. gravity: 9.8 N/kg

    ''' 玩弄几何变量
    '''
    def build_x_denorm(self):
        """ This is core function """
        # this is used in part_evaluation
        GP = self.d['GP']
        self.x_denorm_dict = self.get_x_denorm_dict_from_geometric_parameters(GP)
        x_denorm = [val for key, val in self.x_denorm_dict.items()]
        if False:
            from pprint import pprint
            pprint(x_denorm_dict)
            pprint(x_denorm)
            pprint(GP)
            quit()
        return x_denorm
    def get_x_denorm_dict_from_geometric_parameters(self, GP):
        x_denorm_dict = OrderedDict()
        for key, parameter in GP.items():
            if parameter.type == 'free':
                x_denorm_dict[key] = parameter.value
        return x_denorm_dict
    def get_x_denorm_dict_from_x_denorm_list(self, x_denorm):
        # 先拿个模板来，但是几何尺寸的变量值是旧的
        x_denorm_dict = self.get_x_denorm_dict_from_geometric_parameters(self.d['GP'])

        # print('[inner_rotor_motor.py] DEBUG x_denorm_dict:', x_denorm_dict.keys())

        # 对模板进行遍历，挨个把新的几何尺寸的值从x_denorm中读取出来并更新x_denorm_dict
        for key, new_val in zip(x_denorm_dict.keys(), x_denorm):
            x_denorm_dict[key] = new_val
        return x_denorm_dict
    def update_geometric_parameters_using_x_denorm_dict(self, x_denorm_dict):
        # Update Free Parameters (a.k.a. x_denorm)
        for key, val in x_denorm_dict.items():
            self.d['GP'][key].value = val

        # Update Derived Parameters
        for key, parameter in self.d['GP'].items():
            if parameter.type == 'derived':
                # print(parameter)
                parameter.value = None # 先全部清空，防止编写derive时搞错依赖项关系，更新顺序是有先后的，后面的derived parameter 可以利用前面的derived parameter的值。
        # print()
        count_TypeError = 0
        for key, parameter in self.d['GP'].items():
            if parameter.type == 'derived':
                try:
                    parameter.value = parameter.calc(self.d['GP'], self.SI)
                except TypeError as e: # TypeError: unsupported operand type(s) for -: 'NoneType' and 'NoneType' 用来计算的变量还未被赋值
                    count_TypeError += 1
                    print('[inner_rotor_motor.py] TypeError: None is used for derivation.')
                    pass
                else: # no exception
                    if parameter.value<=0:
                        print('[inner_rotor_motor.py] Negative geometric parameter:')
                        for k,v in self.d['GP'].items():
                            print('\t', k, v)
                        raise Exception('Error: Negative derived parameter', str(parameter))
        # 针对“用来计算的变量还未被赋值”的变量，再次调用它的calc方法。
        while count_TypeError>0:
            for key, parameter in self.d['GP'].items():
                if parameter.type == 'derived' and parameter.value is None:
                    try:
                        parameter.value = parameter.calc(self.d['GP'], self.SI)
                    except TypeError as e: # TypeError: unsupported operand type(s) for -: 'NoneType' and 'NoneType' 用来计算的变量还未被赋值
                        count_TypeError += 1
                        print('[inner_rotor_motor.py] [Re] TypeError: None is used for derivation.')
                        pass
                    finally:
                        count_TypeError -= 1
        return self.d['GP']

class variant_machine_as_objects(object):
    ''' # variant则有点像是具体的电机实现类（由各个局部类，比如转子、定子等组成）
    '''
    def __init__(self, spmsm_template=None, x_denorm=None, counter=None, counter_loop=None, 
                verbose=True):

        self.template = spmsm_template

        #00 Settings
        # self.template.fea_config_dict = spmsm_template.fea_config_dict
        # self.template.spec_input_dict = spmsm_template.spec_input_dict
        # self.spec_geometry_dict = spmsm_template.spec_geometry_dict

        SI = self.template.spec_input_dict

        #01 Model ID
        # self.model_name_prefix
        self.counter = counter
        self.counter_loop = counter_loop
        if counter is not None:
            if counter_loop == 1:
                self.name = f"p{SI['p']}ps{SI['ps']}-Q{SI['Qs']}y{SI['coil_pitch_y']}-{counter}"
            else:
                self.name = f"p{SI['p']}ps{SI['ps']}-Q{SI['Qs']}y{SI['coil_pitch_y']}-{counter}-redo{counter_loop}"
        else:
            self.name = 'SPMSM_InitialDesign'
        self.ID = 'ID'

        #02 Geometry Data
        if x_denorm is None:
            # template as variant
            GP = self.template.d['GP'] # do nothing, use template's GP
        else:
            x_denorm_dict = self.template.get_x_denorm_dict_from_x_denorm_list(x_denorm)
            if verbose:
                for k,v in x_denorm_dict.items():
                    print('\t [inner_rotor_motor.py]', k,v)
            GP = self.template.update_geometric_parameters_using_x_denorm_dict(x_denorm_dict)

        #03 Inherit properties
        # self.template.d['EX']
            # self.DriveW_CurrentAmp = None ########### will be assisned when drawing the coils
            # self.DriveW_poles      = spmsm_template.DriveW_poles

            # self.Js                = spmsm_template.Js 
            # self.fill_factor       = spmsm_template.fill_factor 

            # self.template.d['EX']['mm_template_stack_length']      = spmsm_template.stack_length 


        # #04 Material Condutivity Properties
        # if self.template.fea_config_dict is not None:
        #     self.End_Ring_Resistance = fea_config_dict['End_Ring_Resistance']
        #     self.Bar_Conductivity = fea_config_dict['Bar_Conductivity']
        # self.Copper_Loss = self.DriveW_CurrentAmp**2 / 2 * self.DriveW_Rs * 3
        # # self.Resistance_per_Turn = 0.01 # TODO

        #05 Winidng Excitation
        # self.CurrentAmp_per_phase = None # will be used in copper loss calculation

        #06 Meshing & Solver Properties
        # self.max_nonlinear_iteration = 50 # 30 for transient solve
        # self.meshSize_Magnet = 2 # mm

    def reproduce_wily(self):
        ''' This method is only used for reproducing design from jsonpickle'''
        self.template.d['EX']['wily'] = winding_layout.winding_layout_v2(self.template.SI['DPNV_or_SEPA'], self.template.SI['Qs'], self.template.SI['p'], self.template.SI['ps'], self.template.SI['coil_pitch_y'])

    def update_mechanical_parameters(self, syn_freq=None):
        EX = self.template.d['EX']
        SI = self.template.SI
        if syn_freq is None:
            EX['the_speed'] = EX['DriveW_Freq']*60. / SI['number_of_rotor_pole_pairs'] # rpm
            EX['Omega']     = EX['the_speed'] / 60. * 2*np.pi
            # self.omega = None # This variable name is devil! you can't tell its electrical or mechanical! #+ self.DriveW_Freq * (1-self.the_slip) * 2*pi
        else:
            raise Exception('Not implemented.')

    def get_individual_name(self):
        if self.template.fea_config_dict['flag_optimization'] == True:
            return "ID%s" % (self.ID)
        else:
            return "%s_ID%s" % (self.name, self.ID)

